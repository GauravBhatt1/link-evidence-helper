package linkverify

import (
	"context"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

type staticResolver map[string][]net.IPAddr

func (resolver staticResolver) LookupIPAddr(_ context.Context, host string) ([]net.IPAddr, error) {
	rows, found := resolver[host]
	if !found {
		return nil, errors.New("host not found")
	}
	return rows, nil
}

func TestVerifyUsesBoundedRangeRequestAndReturnsCanonicalMetadata(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodGet || request.Header.Get("Range") != "bytes=0-0" || request.Header.Get("Accept-Encoding") != "identity" {
			t.Fatalf("request method=%s range=%q encoding=%q", request.Method, request.Header.Get("Range"), request.Header.Get("Accept-Encoding"))
		}
		writer.Header().Set("Content-Type", "video/x-matroska")
		writer.Header().Set("Content-Disposition", `attachment; filename="Example Film.mkv"`)
		writer.Header().Set("Content-Range", "bytes 0-0/2147483648")
		writer.WriteHeader(http.StatusPartialContent)
		_, _ = writer.Write([]byte("x"))
	}))
	defer server.Close()

	verifiedAt := time.Date(2026, 8, 5, 3, 0, 0, 0, time.UTC)
	link, err := (Verifier{AllowPrivate: true, Now: func() time.Time { return verifiedAt }}).Verify(
		context.Background(),
		Candidate{SourceID: "source-one", URL: server.URL + "/file.mkv?token=opaque", Quality: "1080p"},
	)
	if err != nil {
		t.Fatal(err)
	}
	if link.URL != server.URL+"/file.mkv?token=opaque" || link.Filename != "Example Film.mkv" || link.Size != "2 GB" || link.Quality != "1080p" || link.SourceID != "source-one" || !link.VerifiedAt.Equal(verifiedAt) {
		t.Fatalf("link = %#v", link)
	}
}

func TestVerifyRejectsHTMLAndUnsafeDNS(t *testing.T) {
	htmlServer := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		writer.Header().Set("Content-Type", "text/html")
		_, _ = writer.Write([]byte("<html>not a file</html>"))
	}))
	defer htmlServer.Close()

	_, err := (Verifier{AllowPrivate: true}).Verify(context.Background(), Candidate{
		SourceID: "html", URL: htmlServer.URL + "/page", Quality: "1080p",
	})
	var failure *Error
	if !errors.As(err, &failure) || failure.Code != "not_delivery" || !failure.Blocked {
		t.Fatalf("HTML error = %#v", err)
	}

	_, err = (Verifier{Resolver: staticResolver{"private.example": {{IP: net.ParseIP("10.0.0.8")}}}}).Verify(
		context.Background(),
		Candidate{SourceID: "private", URL: "http://private.example/file.mkv", Quality: "1080p"},
	)
	if !errors.As(err, &failure) || failure.Code != "unsafe_network" || !failure.Blocked {
		t.Fatalf("private DNS error = %#v", err)
	}
}

func TestVerifyBlocksUnapprovedRedirectOriginAndAllowsExplicitOrigin(t *testing.T) {
	target := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		writer.Header().Set("Content-Type", "application/octet-stream")
		writer.Header().Set("Content-Disposition", `attachment; filename="file.zip"`)
		writer.Header().Set("Content-Range", "bytes 0-0/1024")
		writer.WriteHeader(http.StatusPartialContent)
		_, _ = writer.Write([]byte("x"))
	}))
	defer target.Close()
	redirector := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		http.Redirect(writer, request, target.URL+"/file.zip", http.StatusFound)
	}))
	defer redirector.Close()

	verifier := Verifier{AllowPrivate: true}
	_, err := verifier.Verify(context.Background(), Candidate{
		SourceID: "source", URL: redirector.URL + "/start", Quality: "720p",
	})
	var failure *Error
	if !errors.As(err, &failure) || failure.Code != "unsafe_redirect" || !failure.Blocked {
		t.Fatalf("unapproved redirect error = %#v", err)
	}

	link, err := verifier.Verify(context.Background(), Candidate{
		SourceID: "source", URL: redirector.URL + "/start", Quality: "720p", AllowedOrigins: []string{target.URL},
	})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasSuffix(link.URL, "/file.zip") || link.Size != "1 KB" {
		t.Fatalf("redirected link = %#v", link)
	}
}

func TestVerifyClassifiesTemporaryStatusAndTimeout(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		http.Error(writer, "retry", http.StatusServiceUnavailable)
	}))
	defer server.Close()
	_, err := (Verifier{AllowPrivate: true}).Verify(context.Background(), Candidate{
		SourceID: "temporary", URL: server.URL + "/file.mkv", Quality: "1080p",
	})
	var failure *Error
	if !errors.As(err, &failure) || failure.Code != "http_status" || !failure.Temporary {
		t.Fatalf("status error = %#v", err)
	}

	slow := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		time.Sleep(100 * time.Millisecond)
		writer.Header().Set("Content-Type", "application/octet-stream")
		_, _ = writer.Write([]byte("x"))
	}))
	defer slow.Close()
	_, err = (Verifier{AllowPrivate: true, Timeout: 10 * time.Millisecond}).Verify(context.Background(), Candidate{
		SourceID: "slow", URL: slow.URL + "/file.mkv", Quality: "1080p",
	})
	if !errors.As(err, &failure) || failure.Code != "timeout" || !failure.Temporary {
		t.Fatalf("timeout error = %#v", err)
	}
}

func TestVerifySanitizesFallbackFilename(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		writer.Header().Set("Content-Type", "application/octet-stream")
		writer.Header().Set("Content-Length", "5")
		_, _ = writer.Write([]byte("hello"))
	}))
	defer server.Close()
	link, err := (Verifier{AllowPrivate: true}).Verify(context.Background(), Candidate{
		SourceID: "source", URL: server.URL + "/download", Filename: "../safe-file.mkv", Quality: "1080p",
	})
	if err != nil {
		t.Fatal(err)
	}
	if link.Filename != "safe-file.mkv" || link.Size != "5 B" {
		t.Fatalf("link = %#v", link)
	}
}
