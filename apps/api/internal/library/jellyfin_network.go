package library

import (
	"bytes"
	"context"
	"errors"
	"net"
	"sort"
	"strings"
)

func (client *JellyfinClient) dialContext(ctx context.Context, network, address string) (net.Conn, error) {
	host, port, err := net.SplitHostPort(address)
	if err != nil || !strings.EqualFold(strings.TrimSuffix(host, "."), strings.TrimSuffix(client.baseURL.Hostname(), ".")) {
		return nil, ErrJellyfinUnsafeTarget
	}
	ips, err := resolveTarget(ctx, client.resolver, host)
	if err != nil {
		return nil, ErrJellyfinUnavailable
	}
	allowed := make([]net.IP, 0, len(ips))
	for _, ip := range ips {
		if !deniedJellyfinIP(ip, client.allowPrivate) {
			allowed = append(allowed, ip)
		}
	}
	if len(allowed) == 0 {
		return nil, ErrJellyfinUnsafeTarget
	}
	sort.Slice(allowed, func(left, right int) bool {
		return bytes.Compare(allowed[left], allowed[right]) < 0
	})
	var lastErr error
	for _, ip := range allowed {
		connection, dialErr := client.dialer.DialContext(ctx, network, net.JoinHostPort(ip.String(), port))
		if dialErr == nil {
			return connection, nil
		}
		lastErr = dialErr
	}
	if lastErr != nil {
		return nil, lastErr
	}
	return nil, ErrJellyfinUnavailable
}

func resolveTarget(ctx context.Context, resolver IPResolver, host string) ([]net.IP, error) {
	if ip := net.ParseIP(strings.Trim(host, "[]")); ip != nil {
		return []net.IP{ip}, nil
	}
	addresses, err := resolver.LookupIPAddr(ctx, host)
	if err != nil {
		return nil, err
	}
	ips := make([]net.IP, 0, len(addresses))
	for _, address := range addresses {
		if address.IP != nil {
			ips = append(ips, address.IP)
		}
	}
	if len(ips) == 0 {
		return nil, errors.New("no addresses")
	}
	return ips, nil
}

func deniedJellyfinIP(ip net.IP, allowPrivate bool) bool {
	if ip == nil || ip.IsUnspecified() || ip.IsMulticast() || ip.IsLinkLocalUnicast() || ip.IsLinkLocalMulticast() {
		return true
	}
	if isDocumentationOrSpecialIP(ip) {
		return true
	}
	if ip.IsLoopback() || ip.IsPrivate() {
		return !allowPrivate
	}
	return !ip.IsGlobalUnicast()
}

func isDocumentationOrSpecialIP(ip net.IP) bool {
	for _, cidr := range []string{
		"100.64.0.0/10",
		"192.0.0.0/24",
		"192.0.2.0/24",
		"198.18.0.0/15",
		"198.51.100.0/24",
		"203.0.113.0/24",
		"2001:db8::/32",
	} {
		_, network, _ := net.ParseCIDR(cidr)
		if network.Contains(ip) {
			return true
		}
	}
	return false
}
