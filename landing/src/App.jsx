import { Routes, Route } from 'react-router-dom';
import Navbar from './components/Navbar';
import Footer from './components/Footer';
import Home from './pages/Home';
import About from './pages/About';
import Contribute from './pages/Contribute';

export default function App() {
  return (
    <div className="app">
      <a href="#main" className="skip-link">Skip to main content</a>
      <Navbar />
      <main className="main-content" id="main">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/about" element={<About />} />
          <Route path="/contribute" element={<Contribute />} />
        </Routes>
      </main>
      <Footer />
    </div>
  );
}
