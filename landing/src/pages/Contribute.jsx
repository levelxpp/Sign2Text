import { useState } from 'react';
import { motion } from 'framer-motion';
import { Link } from 'react-router-dom';
import {
  Hand,
  Video,
  CheckCircle,
  Database,
  Users,
  ShieldCheck,
  Globe,
  ArrowRight,
  Sparkles,
  BarChart3,
} from 'lucide-react';
import { APP_URL, ADMIN_API_URL } from '../config';

const fadeUp = {
  hidden: { opacity: 0, y: 28 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.65, ease: 'easeOut' },
  },
};

const stagger = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.12 } },
};

const reasons = [
  {
    Icon: Users,
    title: 'Signer Diversity',
    description:
      'Sign language varies between individuals - hand size, speed, angle, and expression all differ. A model trained on limited signers will only recognize limited signing styles. Broader participant coverage helps Sign2Text work well for more people.',
  },
  {
    Icon: Globe,
    title: 'Vocabulary Coverage',
    description:
      'The current model supports a subset of ASL signs. Every new word or phrase added to the training set directly expands what the system can translate - moving it closer to practical, everyday use.',
  },
  {
    Icon: BarChart3,
    title: 'Recognition Accuracy',
    description:
      'More examples of the same sign - captured in different lighting, backgrounds, and camera angles - help the model distinguish real variation from noise. Volume and variety both improve reliability.',
  },
];

const flowSteps = [
  {
    Icon: Hand,
    number: '01',
    title: 'Choose a Prompt',
    description:
      'Select a word or phrase from a guided prompt list. Each prompt targets a sign that the model needs more training data for.',
  },
  {
    Icon: Video,
    number: '02',
    title: 'Record Your Sign',
    description:
      'Using your webcam, record a short clip of yourself signing the prompted word. No special equipment is needed - just a standard browser and camera.',
  },
  {
    Icon: CheckCircle,
    number: '03',
    title: 'Review & Consent',
    description:
      'Preview your recording before submitting. Confirm the sign is accurate, and provide explicit consent for how your data will be used in training.',
  },
  {
    Icon: Database,
    number: '04',
    title: 'Improve the Dataset',
    description:
      'Approved recordings join a curated training dataset. The model is periodically retrained with new data, improving accuracy and coverage with each cycle.',
  },
];

const trustPrinciples = [
  {
    Icon: ShieldCheck,
    title: 'Informed Consent',
    description:
      'Every contributor will understand exactly how their data is used before submitting. Clear, plain-language consent - no buried terms.',
  },
  {
    Icon: Sparkles,
    title: 'Quality Over Quantity',
    description:
      'Submissions go through a review step before entering the training pipeline. This protects model quality and respects contributor effort.',
  },
  {
    Icon: Users,
    title: 'Clear Program Information',
    description:
      'Sign2Text will explain the contribution process, data handling practices, and model-improvement goals in plain language so participants know what to expect.',
  },
];

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function formatRetryDelay(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) {
    return 'a short moment';
  }
  if (seconds < 60) {
    return `${seconds} second${seconds === 1 ? '' : 's'}`;
  }
  const minutes = Math.ceil(seconds / 60);
  return `${minutes} minute${minutes === 1 ? '' : 's'}`;
}

export default function Contribute() {
  const [email, setEmail] = useState('');
  const [website, setWebsite] = useState('');
  const [status, setStatus] = useState('idle');
  const [feedback, setFeedback] = useState(null);

  const clearFeedback = () => {
    if (status === 'error') {
      setStatus('idle');
    }
    if (feedback) {
      setFeedback(null);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    const normalizedEmail = email.trim().toLowerCase();

    if (!normalizedEmail || !EMAIL_RE.test(normalizedEmail)) {
      setFeedback({
        tone: 'error',
        title: 'Enter a valid email address',
        body: 'Use a standard address like name@example.com so we can notify you when the contributor program opens.',
      });
      setStatus('error');
      return;
    }

    setStatus('loading');
    setFeedback(null);

    try {
      const res = await fetch(`${ADMIN_API_URL}/api/public/contributor-leads`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: normalizedEmail, website })
      });
      const body = await res.json().catch(() => ({}));

      if (res.status === 429) {
        const retryAfter = Number.parseInt(res.headers.get('Retry-After') || '', 10);
        setFeedback({
          tone: 'warning',
          title: 'Please wait before trying again',
          body: Number.isFinite(retryAfter)
            ? `Please wait about ${formatRetryDelay(retryAfter)} and then submit again.`
            : 'Please wait a short moment and then submit again.',
        });
        setStatus('error');
        return;
      }

      if (!res.ok) {
        if (res.status === 400 && typeof body.detail === 'string' && /invalid email address/i.test(body.detail)) {
          setFeedback({
            tone: 'error',
            title: 'Enter a valid email address',
            body: 'Please check the address and try again.',
          });
          setStatus('error');
          return;
        }
        throw new Error(typeof body.detail === 'string' ? body.detail : 'Failed to submit email');
      }

      setStatus('success');
      setFeedback(null);
      setEmail('');
      setWebsite('');
    } catch (err) {
      console.error(err);
      setStatus('error');
      setFeedback({
        tone: 'error',
        title: 'We could not save your request',
        body: 'Please try again in a little while.',
      });
    }
  };

  return (
    <div className="page page-contribute">
      <section className="contrib-hero" id="contribute-hero">
        <motion.div
          className="contrib-hero-inner"
          initial="hidden"
          animate="visible"
          variants={stagger}
        >
          <motion.span className="section-label" variants={fadeUp}>
            Contributor Program
          </motion.span>
          <motion.h1 className="contrib-hero-title" variants={fadeUp}>
            Help improve future Sign2Text recognition.
          </motion.h1>
          <motion.p className="contrib-hero-lead" variants={fadeUp}>
            Sign2Text is preparing a contributor program where signers can
            record ASL examples directly from their browser. Broader, better
            data helps the application recognize more signing styles with
            greater reliability.
          </motion.p>
          <motion.div className="contrib-hero-badge" variants={fadeUp}>
            <span className="coming-soon-badge">Planned Program</span>
          </motion.div>
        </motion.div>
      </section>

      <section className="section contrib-section" id="contribute-why" aria-labelledby="contrib-why-heading">
        <motion.div
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: '-80px' }}
          variants={fadeUp}
        >
          <span className="section-label">Why It Matters</span>
          <h2 className="section-title" id="contrib-why-heading">
            Machine learning needs real-world data
          </h2>
          <p className="section-subtitle">
            ASL recognition models learn from examples. The more diverse and
            representative the training data, the better the system works for
            everyone - not just the signers it was originally trained on.
          </p>
        </motion.div>

        <motion.div
          className="contrib-reasons-grid"
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: '-60px' }}
          variants={stagger}
        >
          {reasons.map(({ Icon, title, description }) => (
            <motion.div
              key={title}
              className="contrib-reason-card"
              variants={fadeUp}
            >
              <div className="contrib-reason-icon-wrap">
                <Icon size={22} aria-hidden="true" />
              </div>
              <h3 className="contrib-reason-title">{title}</h3>
              <p className="contrib-reason-desc">{description}</p>
            </motion.div>
          ))}
        </motion.div>
      </section>

      <section className="section contrib-section" id="contribute-flow" aria-labelledby="contrib-flow-heading">
        <motion.div
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: '-80px' }}
          variants={fadeUp}
        >
          <span className="section-label">Planned Workflow</span>
          <h2 className="section-title" id="contrib-flow-heading">How the contributor program will work</h2>
          <p className="section-subtitle">
            The contribution flow is designed to be simple, browser-based, and
            respectful of every participant&apos;s time and data.
          </p>
        </motion.div>

        <motion.div
          className="contrib-flow-grid"
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: '-40px' }}
          variants={stagger}
        >
          {flowSteps.map(({ Icon, number, title, description }) => (
            <motion.div
              key={number}
              className="contrib-flow-step"
              variants={fadeUp}
            >
              <span className="contrib-flow-number" aria-hidden="true">
                {number}
              </span>
              <div className="contrib-flow-icon-wrap">
                <Icon size={20} aria-hidden="true" />
              </div>
              <h3 className="contrib-flow-label">{title}</h3>
              <p className="contrib-flow-text">{description}</p>
            </motion.div>
          ))}
        </motion.div>
      </section>

      <motion.section
        className="section contrib-section"
        id="contribute-impact"
        initial="hidden"
        whileInView="visible"
        viewport={{ once: true, margin: '-80px' }}
        variants={fadeUp}
        aria-labelledby="contrib-impact-heading"
      >
        <span className="section-label">Your Impact</span>
        <h2 className="section-title" id="contrib-impact-heading">What your data can improve</h2>
        <div className="contrib-prose">
          <p>
            Each recording submitted through the future contribution portal
            directly supports specific improvements. Signing a new word helps
            expand vocabulary coverage. Recording a word the model already knows
            - but in your own style, lighting, and angle - helps it generalize
            better. Signing with different speeds or expressions helps the
            system handle the natural variation in real conversations.
          </p>
          <p>
            Over time, a diverse contributor base creates a training set that
            represents how ASL is actually used - not just how it appears in
            controlled settings. That is what helps move recognition from
            limited coverage toward something people can trust more often in
            everyday communication.
          </p>
        </div>
      </motion.section>

      <section
        className="section contrib-section contrib-trust-section"
        id="contribute-trust"
        aria-labelledby="contrib-trust-heading"
      >
        <motion.div
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: '-80px' }}
          variants={fadeUp}
        >
          <span className="section-label">Trust & Transparency</span>
          <h2 className="section-title" id="contrib-trust-heading">Built on responsible practices</h2>
          <p className="section-subtitle">
            Collecting sign language data carries real responsibility.
            The contribution program will be built with clear safeguards from
            the start.
          </p>
        </motion.div>

        <motion.div
          className="contrib-trust-grid"
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: '-60px' }}
          variants={stagger}
        >
          {trustPrinciples.map(({ Icon, title, description }) => (
            <motion.div
              key={title}
              className="contrib-trust-card"
              variants={fadeUp}
            >
              <div className="contrib-trust-icon-wrap">
                <Icon size={20} aria-hidden="true" />
              </div>
              <h3 className="contrib-trust-title">{title}</h3>
              <p className="contrib-trust-desc">{description}</p>
            </motion.div>
          ))}
        </motion.div>
      </section>

      <section className="section contrib-section" id="contribute-context" aria-labelledby="contrib-context-heading">
        <motion.div
          className="contrib-context-card"
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: '-80px' }}
          variants={fadeUp}
        >
          <span className="section-label">The Bigger Picture</span>
          <h2 className="section-title" id="contrib-context-heading">
            Broader data supports better accessibility
          </h2>
          <div className="contrib-context-body">
            <p>
              Sign2Text currently supports ASL translation through three input
              modes - full signs, fingerspelling, and voice. The models behind
              these modes were trained on existing datasets, but those datasets
              have limits: limited signers, limited vocabulary, limited variety.
            </p>
            <p>
              Contributor participation is one of the clearest ways to overcome
              those limits. A broader dataset means the system can recognize
              more signs, handle more signing styles, and work more reliably
              across different environments. Each contribution helps make
              Sign2Text more useful for everyday communication.
            </p>
          </div>
        </motion.div>
      </section>

      <section className="section contrib-section contrib-cta-final" id="contribute-cta" aria-labelledby="contrib-cta-heading">
        <motion.div
          className="contrib-cta-card"
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: '-80px' }}
          variants={fadeUp}
        >
          <div className="contrib-cta-badge">
            <span className="coming-soon-badge">Coming Soon</span>
          </div>
          <h2 className="section-title" id="contrib-cta-heading">
            Join the contributor interest list
          </h2>
          <p className="contrib-cta-desc">
            The contributor program is not live yet, but it is actively being
            prepared. When it opens, participants will be able to record ASL
            signs directly from their browser - no downloads, no accounts
            required.
          </p>
          <p className="contrib-cta-note">
            Join the list to hear when the contributor program opens, or try
            the current translation experience below.
          </p>

          {status === 'success' ? (
            <motion.div
              className="contrib-success-message"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              aria-live="polite"
            >
              <CheckCircle size={28} className="success-icon" />
              <div className="success-text">
                <p className="success-title">You&apos;re on the list.</p>
                <p>We&apos;ll email you when the contributor program is ready.</p>
                <p>If this address was already registered, you&apos;re still all set.</p>
              </div>
            </motion.div>
          ) : (
            <form onSubmit={handleSubmit} className="contrib-form">
              <div aria-hidden="true" style={{ position: 'absolute', opacity: 0, zIndex: -1, pointerEvents: 'none' }}>
                <input
                  type="text"
                  name="website"
                  tabIndex={-1}
                  autoComplete="off"
                  value={website}
                  onChange={(e) => setWebsite(e.target.value)}
                />
              </div>

              <div className="contrib-form-group">
                <input
                  type="email"
                  className="contrib-input"
                  placeholder="Enter your email address"
                  value={email}
                  onChange={(e) => {
                    clearFeedback();
                    setEmail(e.target.value);
                  }}
                  disabled={status === 'loading'}
                  required
                />
                <button
                  type="submit"
                  className="btn btn-primary contrib-submit"
                  disabled={status === 'loading'}
                >
                  {status === 'loading' ? 'Submitting...' : 'Notify Me'}
                </button>
              </div>
              {status === 'error' && feedback && (
                <div
                  className={`contrib-form-notice contrib-form-notice-${feedback.tone}`}
                  role="status"
                  aria-live="polite"
                >
                  <p className="contrib-form-notice-title">{feedback.title}</p>
                  <p>{feedback.body}</p>
                </div>
              )}
              <p className="contrib-form-help">
                Use an email you check regularly. If you&apos;ve already signed up,
                there is no need to submit again.
              </p>
              <p className="contrib-form-legal">
                By submitting, you acknowledge our{' '}
                <a href="/privacy.html">Privacy Policy</a> and{' '}
                <a href="/terms.html">Terms of Use</a>.
              </p>
            </form>
          )}

          <div className="contrib-cta-actions" style={{ marginTop: '2.5rem' }}>
            <a
              href={APP_URL}
              className="btn btn-secondary"
              target="_blank"
              rel="noopener noreferrer"
              id="contribute-try-app"
            >
              Try the App <ArrowRight size={16} />
            </a>
            <Link
              to="/about"
              className="btn btn-secondary"
              id="contribute-learn-more"
            >
              Learn About Sign2Text
            </Link>
          </div>
          <p className="contrib-disclaimer">
            Interest list requests are submitted through a secure signup
            service.
          </p>
        </motion.div>
      </section>
    </div>
  );
}
