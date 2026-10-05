import { Link } from 'react-router-dom';

export default function Footer() {
  const year = new Date().getFullYear();

  return (
    <footer className="footer" id="main-footer" role="contentinfo">
      <div className="footer-inner">
        <div className="footer-brand">
          <div className="footer-brand-row">
            <span className="brand-icon" aria-hidden="true">{'\u2726'}</span>
            <span className="brand-text">Sign2Text</span>
          </div>
          <p className="footer-tagline">Making communication accessible for everyone.</p>
        </div>

        <nav className="footer-links" aria-label="Footer navigation">
          <div className="footer-col">
            <h4 className="footer-heading">Pages</h4>
            <Link to="/" className="footer-link">Home</Link>
            <Link to="/about" className="footer-link">About</Link>
            <Link to="/contribute" className="footer-link">Contribute</Link>
          </div>
          <div className="footer-col">
            <h4 className="footer-heading">Resources</h4>
            <a href="mailto:contact@sign2text.dev" className="footer-link">Contact</a>
          </div>
          <div className="footer-col">
            <h4 className="footer-heading">Legal</h4>
            <a href="/privacy.html" className="footer-link">Privacy Policy</a>
            <a href="/terms.html" className="footer-link">Terms of Use</a>
          </div>
        </nav>

        <div className="footer-bottom">
          <p>&copy; {year} Sign2Text. Accessibility-focused communication application.</p>
        </div>
      </div>
    </footer>
  );
}
