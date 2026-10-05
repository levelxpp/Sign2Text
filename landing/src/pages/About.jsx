import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import { Hand, Mic, Type, Camera, Cpu, Monitor, ArrowRight } from 'lucide-react';
import { APP_URL } from '../config';

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

const modes = [
  {
    Icon: Hand,
    name: 'ASL Mode',
    accent: 'var(--color-accent)',
    description:
      'Detects full American Sign Language signs through your webcam. The system follows movement over time so it can recognize complete signs, not just isolated snapshots.',
  },
  {
    Icon: Mic,
    name: 'Voice Mode',
    accent: 'var(--color-feature-voice)',
    description:
      'Captures spoken words using browser-native speech recognition. Voice Mode supports conversations where speech and sign are used together, keeping the transcript continuous without manual switching.',
  },
  {
    Icon: Type,
    name: 'Letter Mode',
    accent: 'var(--color-feature-letter)',
    description:
      'Recognizes individual fingerspelled letters in real time, helping users spell names, technical terms, and unfamiliar words character by character.',
  },
];

const flowSteps = [
  {
    Icon: Camera,
    label: 'Capture',
    text: 'Your browser camera captures hand movements and poses at real-time frame rates.',
  },
  {
    Icon: Cpu,
    label: 'Process',
    text: 'Pose estimation and deep learning models interpret the input into recognized signs, letters, or words.',
  },
  {
    Icon: Monitor,
    label: 'Display',
    text: 'Translated text appears instantly on screen, creating a live caption stream of the conversation.',
  },
];

export default function About() {
  return (
    <div className="page page-about">
      <section className="about-hero" id="about-hero">
        <motion.div
          className="about-hero-inner"
          initial="hidden"
          animate="visible"
          variants={stagger}
        >
          <motion.span className="section-label" variants={fadeUp}>
            About Sign2Text
          </motion.span>
          <motion.h1 className="about-hero-title" variants={fadeUp}>
            Communication should work for everyone.
          </motion.h1>
          <motion.p className="about-hero-lead" variants={fadeUp}>
            Sign2Text is a browser-based accessibility application that
            translates American Sign Language, voice, and fingerspelling into
            live text. It is designed to make real-time communication easier to
            access without requiring downloads, accounts, or specialized
            hardware.
          </motion.p>
        </motion.div>
      </section>

      <motion.section
        className="section about-section"
        id="about-problem"
        initial="hidden"
        whileInView="visible"
        viewport={{ once: true, margin: '-80px' }}
        variants={fadeUp}
        aria-labelledby="about-problem-heading"
      >
        <span className="section-label">The Gap</span>
        <h2 className="section-title" id="about-problem-heading">Why This Needs to Exist</h2>
        <div className="about-prose">
          <p>
            Over 70 million people worldwide use sign language as their primary
            means of communication. Despite this, most digital tools assume
            everyone communicates through text or voice. Video calls do not
            generate captions for signers. Chat apps do not accept gesture
            input. The result is a persistent gap between how these tools are
            built and how millions of people actually communicate.
          </p>
          <p>
            Sign2Text addresses a specific piece of this problem: giving
            signers a way to produce live text output from their natural
            language in real time, in a browser, with no intermediary. It is
            not a complete solution to communication accessibility, but it is a
            meaningful step toward closing the gap.
          </p>
        </div>
      </motion.section>

      <section className="section about-section about-modes-section" id="about-modes" aria-labelledby="about-modes-heading">
        <motion.div
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: '-80px' }}
          variants={fadeUp}
        >
          <span className="section-label">Capabilities</span>
          <h2 className="section-title" id="about-modes-heading">Three Input Modes, One Experience</h2>
          <p className="section-subtitle">
            Each mode handles a different aspect of sign language communication.
            They work independently or together within the same session.
          </p>
        </motion.div>

        <motion.div
          className="about-modes-detail-grid"
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: '-60px' }}
          variants={stagger}
        >
          {modes.map(({ Icon, name, accent, description }) => (
            <motion.div
              key={name}
              className="about-mode-detail"
              variants={fadeUp}
              style={{ '--mode-accent': accent }}
            >
              <div className="about-mode-icon-wrap">
                <Icon size={22} aria-hidden="true" />
              </div>
              <h3 className="about-mode-name">{name}</h3>
              <p className="about-mode-desc">{description}</p>
            </motion.div>
          ))}
        </motion.div>
      </section>

      <section className="section about-section" id="about-flow" aria-labelledby="about-flow-heading">
        <motion.div
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: '-80px' }}
          variants={fadeUp}
        >
          <span className="section-label">How It Works</span>
          <h2 className="section-title" id="about-flow-heading">From Gesture to Text</h2>
          <p className="section-subtitle">
            The entire pipeline runs in the browser - no server uploads, no
            cloud processing. Your camera feed stays on your device.
          </p>
        </motion.div>

        <motion.div
          className="about-flow-grid"
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: '-40px' }}
          variants={stagger}
        >
          {flowSteps.map(({ Icon, label, text }, idx) => (
            <motion.div key={label} className="about-flow-step" variants={fadeUp}>
              <span className="about-flow-number" aria-hidden="true">{String(idx + 1).padStart(2, '0')}</span>
              <div className="about-flow-icon-wrap">
                <Icon size={20} aria-hidden="true" />
              </div>
              <h3 className="about-flow-label">{label}</h3>
              <p className="about-flow-text">{text}</p>
            </motion.div>
          ))}
        </motion.div>
      </section>

      <motion.section
        className="section about-section"
        id="about-difference"
        initial="hidden"
        whileInView="visible"
        viewport={{ once: true, margin: '-80px' }}
        variants={fadeUp}
        aria-labelledby="about-diff-heading"
      >
        <span className="section-label">Design Choices</span>
        <h2 className="section-title" id="about-diff-heading">Built Around Real Constraints</h2>
        <div className="about-prose">
          <p>
            Sign2Text runs entirely in the browser. There is no app to install,
            no account to create, and no data stored on remote servers. This
            was a deliberate design decision - accessibility tools should be as
            frictionless as possible for the people who need them most.
          </p>
          <p>
            The system supports three distinct input modes because sign language
            communication is rarely just one thing. A signer might sign a full
            phrase, fingerspell a proper noun, and voice a clarification all
            within the same sentence. Sign2Text is built to handle that
            multimodal reality, not just isolated gestures.
          </p>
        </div>
      </motion.section>

      <section className="section about-section about-vision-section" id="about-vision" aria-labelledby="about-vision-heading">
        <motion.div
          className="about-vision-card"
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: '-80px' }}
          variants={fadeUp}
        >
          <span className="section-label">Looking Ahead</span>
          <h2 className="section-title" id="about-vision-heading">Where This Goes Next</h2>
          <div className="about-vision-body">
            <p>
              Sign2Text continues to expand. The current experience supports
              ASL, and the platform is designed to support additional sign
              languages as more training data becomes available. Contributor
              recordings can help improve accuracy and broaden future language
              coverage.
            </p>
            <p>
              Ongoing work is focused on improving recognition reliability
              across diverse signing styles, lighting conditions, and skin
              tones. The goal is a tool that feels dependable in real
              communication and improves steadily as the system learns from
              broader input.
            </p>
          </div>
        </motion.div>
      </section>

      <section className="section about-section about-cta-section" id="about-cta" aria-labelledby="about-cta-heading">
        <motion.div
          className="about-cta-card"
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: '-80px' }}
          variants={fadeUp}
        >
          <h2 className="section-title" id="about-cta-heading">See It in Action</h2>
          <p className="about-cta-desc">
            Sign2Text runs directly in your browser. Open the app, allow camera
            access, and start signing - no setup required.
          </p>
          <div className="about-cta-actions">
            <a
              href={APP_URL}
              className="btn btn-primary"
              target="_blank"
              rel="noopener noreferrer"
              id="about-try-app"
            >
              Try the App <ArrowRight size={16} />
            </a>
            <Link to="/contribute" className="btn btn-secondary" id="about-contribute">
              Join Interest List
            </Link>
          </div>
        </motion.div>
      </section>
    </div>
  );
}
