import React, { useRef, useEffect } from 'react';
import { motion } from 'framer-motion';
import { ArrowRight, Sparkles } from 'lucide-react';
import { Link } from 'react-router-dom';
import { APP_URL } from '../config';

/**
 * Interactive particle canvas hero for Sign2Text.
 * Adapted from the current landing aesthetic with a mouse-reactive
 * particle field and staggered fade-up text.
 */
export default function ParticleHero() {
  const canvasRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    let animationFrameId;
    let particles = [];
    const mouse = { x: null, y: null, radius: 180 };

    class Particle {
      constructor(x, y, dx, dy, size, color) {
        this.x = x;
        this.y = y;
        this.dx = dx;
        this.dy = dy;
        this.size = size;
        this.color = color;
      }

      draw() {
        ctx.beginPath();
        ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2, false);
        ctx.fillStyle = this.color;
        ctx.fill();
      }

      update() {
        if (this.x > canvas.width || this.x < 0) this.dx = -this.dx;
        if (this.y > canvas.height || this.y < 0) this.dy = -this.dy;

        if (mouse.x !== null && mouse.y !== null) {
          const mx = mouse.x - this.x;
          const my = mouse.y - this.y;
          const dist = Math.sqrt(mx * mx + my * my);
          if (dist < mouse.radius + this.size) {
            const force = (mouse.radius - dist) / mouse.radius;
            this.x -= (mx / dist) * force * 4;
            this.y -= (my / dist) * force * 4;
          }
        }

        this.x += this.dx;
        this.y += this.dy;
        this.draw();
      }
    }

    function init() {
      particles = [];
      const count = Math.min((canvas.width * canvas.height) / 11000, 120);
      for (let i = 0; i < count; i++) {
        const size = Math.random() * 1.8 + 0.8;
        const x = Math.random() * (canvas.width - size * 4) + size * 2;
        const y = Math.random() * (canvas.height - size * 4) + size * 2;
        const dx = (Math.random() - 0.5) * 0.35;
        const dy = (Math.random() - 0.5) * 0.35;
        const colors = [
          'rgba(34, 211, 238, 0.85)',
          'rgba(6, 182, 212, 0.7)',
          'rgba(103, 232, 249, 0.65)',
          'rgba(165, 243, 252, 0.5)',
        ];
        const color = colors[Math.floor(Math.random() * colors.length)];
        particles.push(new Particle(x, y, dx, dy, size, color));
      }
    }

    function connect() {
      const maxDist = (canvas.width / 8) * (canvas.height / 8);
      for (let a = 0; a < particles.length; a++) {
        for (let b = a + 1; b < particles.length; b++) {
          const distSq =
            (particles[a].x - particles[b].x) ** 2 +
            (particles[a].y - particles[b].y) ** 2;

          if (distSq < maxDist) {
            const opacity = 1 - distSq / 20000;

            if (mouse.x !== null) {
              const dMouse = Math.hypot(
                particles[a].x - mouse.x,
                particles[a].y - mouse.y
              );
              ctx.strokeStyle =
                dMouse < mouse.radius
                  ? `rgba(255, 255, 255, ${opacity})`
                  : `rgba(34, 211, 238, ${opacity * 0.45})`;
            } else {
              ctx.strokeStyle = `rgba(34, 211, 238, ${opacity * 0.45})`;
            }

            ctx.lineWidth = 0.8;
            ctx.beginPath();
            ctx.moveTo(particles[a].x, particles[a].y);
            ctx.lineTo(particles[b].x, particles[b].y);
            ctx.stroke();
          }
        }
      }
    }

    function animate() {
      animationFrameId = requestAnimationFrame(animate);
      ctx.fillStyle = 'rgba(10, 14, 23, 0.92)';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      for (let i = 0; i < particles.length; i++) {
        particles[i].update();
      }
      connect();
    }

    function resize() {
      canvas.width = canvas.parentElement.offsetWidth;
      canvas.height = canvas.parentElement.offsetHeight;
      init();
    }

    const handleMouseMove = (e) => {
      const rect = canvas.getBoundingClientRect();
      mouse.x = e.clientX - rect.left;
      mouse.y = e.clientY - rect.top;
    };
    const handleMouseOut = () => {
      mouse.x = null;
      mouse.y = null;
    };

    window.addEventListener('resize', resize);
    canvas.addEventListener('mousemove', handleMouseMove);
    canvas.addEventListener('mouseout', handleMouseOut);

    resize();
    animate();

    return () => {
      window.removeEventListener('resize', resize);
      canvas.removeEventListener('mousemove', handleMouseMove);
      canvas.removeEventListener('mouseout', handleMouseOut);
      cancelAnimationFrame(animationFrameId);
    };
  }, []);

  const fadeUp = {
    hidden: { opacity: 0, y: 24 },
    visible: (i) => ({
      opacity: 1,
      y: 0,
      transition: { delay: i * 0.18 + 0.4, duration: 0.75, ease: 'easeOut' },
    }),
  };

  return (
    <section className="particle-hero" id="hero">
      <canvas ref={canvasRef} className="particle-canvas" aria-hidden="true" />

      <div className="particle-hero-content">
        <motion.div
          custom={0}
          variants={fadeUp}
          initial="hidden"
          animate="visible"
          className="hero-pill"
        >
          <Sparkles size={15} className="hero-pill-icon" />
          <span>Real-Time Sign Language Translation</span>
        </motion.div>

        <motion.h1
          custom={1}
          variants={fadeUp}
          initial="hidden"
          animate="visible"
          className="particle-hero-title"
        >
          Sign2Text
        </motion.h1>

        <motion.p
          custom={2}
          variants={fadeUp}
          initial="hidden"
          animate="visible"
          className="particle-hero-subtitle"
        >
          Translate American Sign Language, voice, and fingerspelling into live
          text in your browser for clearer everyday communication.
        </motion.p>

        <motion.div
          custom={3}
          variants={fadeUp}
          initial="hidden"
          animate="visible"
          className="particle-hero-actions"
        >
          <a
            href={APP_URL}
            className="btn btn-hero-primary"
            target="_blank"
            rel="noopener noreferrer"
            id="hero-try-app"
          >
            Try the App
            <ArrowRight size={18} />
          </a>
          <Link to="/contribute" className="btn btn-hero-secondary" id="hero-contribute">
            Join Interest List
          </Link>
        </motion.div>
      </div>
    </section>
  );
}
