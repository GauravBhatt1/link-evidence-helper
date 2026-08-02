export function SkipLink() {
  const focusMain = () => {
    window.requestAnimationFrame(() => document.getElementById("main-content")?.focus());
  };
  return <a className="skip-link" href="#main-content" onClick={focusMain}>Skip to main content</a>;
}
