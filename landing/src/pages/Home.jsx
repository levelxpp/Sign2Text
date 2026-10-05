import { useState } from 'react';
import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import { Hand, Mic, Type, Globe, Users, TrendingUp, ArrowRight, CheckCircle } from 'lucide-react';
import ParticleHero from '../components/ParticleHero';
import { APP_URL, ADMIN_API_URL } from '../config';

const fadeUp = {
  hidden: { opacity: 0, y: 28 },
  visible: (i = 0) => ({
    opacity: 1,
    y: 0,
    transition: { delay: i * 0.12, duration: 0.65, ease: 'easeOut' },
  }),
};

const stagger = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.12 } },
};

const features = [
  {
    Icon: Hand,
    title: 'ASL Mode',
    desc: 'Translate full American Sign Language signs in real time with browser-based webcam tracking.',
    accent: 'var(--color-accent)',
  },
  {
    Icon: Mic,
    title: 'Voice Mode',
    desc: 'Turn spoken words into text for conversations that combine voice input with signing.',
    accent: 'var(--color-feature-voice)',
  },
  {
    Icon: Type,
    title: 'Letter Mode',
    desc: 'Fingerspell letters and build words character by character with live hand tracking.',
    accent: 'var(--color-feature-letter)',
  },
];

const stats = [
  { value: '70M+', label: 'Deaf people worldwide', Icon: Globe },
  { value: '300+', label: 'Sign languages in use', Icon: Users },
  { value: 'Real-Time', label: 'Translation speed', Icon: TrendingUp },
];

function formatRetryDelay(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return 'a short moment';
  if (seconds < 60) return `${seconds} second${seconds === 1 ? '' : 's'}`;
  const minutes = Math.ceil(seconds / 60);
  return `${minutes} minute${minutes === 1 ? '' : 's'}`;
}

export default function Home() {
  const [fbName, setFbName] = useState('');
  const [fbMessage, setFbMessage] = useState('');
  const [fbWebsite, setFbWebsite] = useState('');
  const [fbStatus, setFbStatus] = useState('idle');
  const [fbNotice, setFbNotice] = useState(null);

  const clearNotice = () => {
    if (fbStatus === 'error') setFbStatus('idle');
    if (fbNotice) setFbNotice(null);
  };

  const handleFeedbackSubmit = async (e) => {
    e.preventDefault();
    const trimmedMessage = fbMessage.trim();

    if (!trimmedMessage) {
      setFbNotice({ tone: 'error', text: 'Write a message before submitting.' });
      setFbStatus('error');
      return;
    }

    setFbStatus('loading');
    setFbNotice(null);

    try {
      const res = await fetch(`${ADMIN_API_URL}/api/public/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: fbName.trim(),
          message: trimmedMessage,
          source: 'home',
          website: fbWebsite,
        }),
      });

      if (res.status === 429) {
        const retryAfter = Number.parseInt(res.headers.get('Retry-After') || '', 10);
        setFbNotice({
          tone: 'warning',
          text: Number.isFinite(retryAfter)
            ? `Please wait about ${formatRetryDelay(retryAfter)} and try again.`
            : 'Please wait a short moment and try again.',
        });
        setFbStatus('error');
        return;
      }

      if (!res.ok) {
        throw new Error('Request failed');
      }

      setFbStatus('success');
      setFbNotice(null);
      setFbName('');
      setFbMessage('');
      setFbWebsite('');
    } catch {
      setFbStatus('error');
      setFbNotice({ tone: 'error', text: 'Could not send feedback right now. Please try again.' });
    }
  };

  return (
    <div className="page page-home">
      <ParticleHero />

      <section className="section features" id="features" aria-labelledby="features-heading">
        <motion.div
          className="features-header"
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: '-80px' }}
          variants={fadeUp}
        >
          <span className="section-label">Capabilities</span>
          <h2 className="section-title" id="features-heading">Three Modes of Translation</h2>
          <p className="section-subtitle">
            Sign2Text supports multiple input methods to make communication
            flexible and accessible for everyone.
          </p>
        </motion.div>

        <motion.div
          className="features-grid"
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: '-60px' }}
          variants={stagger}
        >
          {features.map(({ Icon, title, desc, accent }, idx) => (
            <motion.div
              key={title}
              className="feature-card"
              id={`feature-${title.toLowerCase().replace(' ', '-')}`}
              variants={fadeUp}
              custom={idx}
            >
              <div className="feature-icon-wrap" style={{ '--card-accent': accent }}>
                <Icon size={24} aria-hidden="true" />
              </div>
              <h3 className="feature-name">{title}</h3>
              <p className="feature-desc">{desc}</p>
              <span className="feature-link" aria-hidden="true">
                Learn more <ArrowRight size={14} />
              </span>
            </motion.div>
          ))}
        </motion.div>
      </section>

      <section className="section stats" id="stats" aria-label="Key statistics">
        <motion.div
          className="stats-grid"
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: '-60px' }}
          variants={stagger}
        >
          {stats.map(({ value, label, Icon }, idx) => (
            <motion.div key={label} className="stat-card" variants={fadeUp} custom={idx}>
              <Icon size={20} className="stat-icon" aria-hidden="true" />
              <span className="stat-value">{value}</span>
              <span className="stat-label">{label}</span>
            </motion.div>
          ))}
        </motion.div>
      </section>

      <section className="section mission" id="mission" aria-labelledby="mission-heading">
        <motion.div
          className="mission-content"
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: '-80px' }}
          variants={fadeUp}
        >
          <span className="section-label">Our Purpose</span>
          <h2 className="section-title" id="mission-heading">Why This Matters</h2>
          <p className="mission-text">
            Over 70 million Deaf people worldwide use sign languages as their
            primary language. Yet most communication tools still are not built
            for them. Sign2Text is built to help close that gap with real-time,
            browser-based translation that is easier to access in everyday
            settings.
          </p>
          <div className="mission-actions">
            <Link to="/about" className="btn btn-ghost" id="mission-learn-more">
              Read our story <ArrowRight size={16} />
            </Link>
          </div>
        </motion.div>
      </section>

      <section className="section feedback" id="feedback" aria-labelledby="feedback-heading">
        <motion.div
          className="feedback-inner"
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: '-80px' }}
          variants={fadeUp}
        >
          <span className="section-label">Feedback</span>
          <h2 className="section-title" id="feedback-heading">Share Your Thoughts</h2>
          <p className="section-subtitle">
            Have a suggestion or something to share? We&apos;d love to hear from you.
          </p>

          {fbStatus === 'success' ? (
            <motion.div
              className="contrib-success-message"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              aria-live="polite"
            >
              <CheckCircle size={28} className="success-icon" />
              <div className="success-text">
                <p className="success-title">Thanks for your feedback.</p>
                <p>Your message has been received.</p>
              </div>
            </motion.div>
          ) : (
            <form onSubmit={handleFeedbackSubmit} className="feedback-form">
              <div aria-hidden="true" style={{ position: 'absolute', opacity: 0, zIndex: -1, pointerEvents: 'none' }}>
                <input
                  type="text"
                  name="website"
                  tabIndex={-1}
                  autoComplete="off"
                  value={fbWebsite}
                  onChange={(e) => setFbWebsite(e.target.value)}
                />
              </div>

              <input
                type="text"
                className="contrib-input"
                placeholder="Name (optional)"
                value={fbName}
                onChange={(e) => { clearNotice(); setFbName(e.target.value); }}
                disabled={fbStatus === 'loading'}
                aria-label="Name"
              />
              <textarea
                className="contrib-input feedback-textarea"
                placeholder="Your feedback..."
                value={fbMessage}
                onChange={(e) => { clearNotice(); setFbMessage(e.target.value); }}
                disabled={fbStatus === 'loading'}
                rows={4}
                aria-label="Feedback message"
              />
              <button
                type="submit"
                className="btn btn-primary"
                disabled={fbStatus === 'loading'}
              >
                {fbStatus === 'loading' ? 'Sending...' : 'Send Feedback'}
              </button>

              {fbStatus === 'error' && fbNotice && (
                <div
                  className={`contrib-form-notice contrib-form-notice-${fbNotice.tone}`}
                  role="status"
                  aria-live="polite"
                >
                  <p>{fbNotice.text}</p>
                </div>
              )}
            </form>
          )}
        </motion.div>
      </section>

      <section className="section contribute-preview" id="contribute-preview" aria-labelledby="preview-heading">
        <motion.div
          className="contribute-preview-inner"
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: '-80px' }}
          variants={fadeUp}
        >
          <div className="contribute-preview-text">
            <span className="section-label">Get Involved</span>
            <h2 className="section-title" id="preview-heading">Help Improve Sign2Text</h2>
            <p className="contribute-preview-desc">
              Contributor participation helps improve recognition across
              different signing styles, backgrounds, and environments. Join the
              interest list to hear when the program opens and help shape more
              reliable translation.
            </p>
            <div className="contribute-preview-actions">
              <Link to="/contribute" className="btn btn-primary" id="preview-contribute">
                Join Interest List <ArrowRight size={16} />
              </Link>
              <a
                href={APP_URL}
                className="btn btn-secondary"
                target="_blank"
                rel="noopener noreferrer"
                id="preview-try-app"
              >
                Try the App
              </a>
            </div>
          </div>
        </motion.div>
      </section>
    </div>
  );
}
