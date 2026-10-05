import { NavLink } from 'react-router-dom';
import { APP_URL } from '../config';

export default function Navbar() {
  return (
    <nav className="navbar" id="main-nav" role="navigation" aria-label="Main navigation">
      <div className="navbar-inner">
        <NavLink to="/" className="navbar-brand" aria-label="Sign2Text — Home">
          <img
            src="/favicon.png"
            alt=""
            aria-hidden="true"
            className="brand-logo"
            width="32"
            height="32"
          />
          <span className="brand-text">Sign2Text</span>
        </NavLink>

        <div className="navbar-links">
          <NavLink
            to="/"
            className={({ isActive }) => isActive ? 'nav-link active' : 'nav-link'}
            end
          >
            Home
          </NavLink>
          <NavLink
            to="/about"
            className={({ isActive }) => isActive ? 'nav-link active' : 'nav-link'}
          >
            About
          </NavLink>
          <NavLink
            to="/contribute"
            className={({ isActive }) => isActive ? 'nav-link active' : 'nav-link'}
          >
            Contribute
          </NavLink>
          <a
            href={APP_URL}
            className="nav-cta"
            target="_blank"
            rel="noopener noreferrer"
            id="nav-try-app"
          >
            Try the App
          </a>
        </div>
      </div>
    </nav>
  );
}
