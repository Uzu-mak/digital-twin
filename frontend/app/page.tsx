import Image from "next/image";
import {
  ArrowDownRight,
  ArrowUpRight,
  Braces,
  Mail,
  Network,
  Workflow,
} from "lucide-react";
import ProjectDirectory from "@/components/project-directory";
import Twin from "@/components/twin";

const skillGroups = [
  {
    label: "Engineering",
    icon: Braces,
    skills: [
      "Python",
      "FastAPI",
      "PostgreSQL",
      "SQLAlchemy",
      "React",
      "Next.js",
      "TypeScript",
    ],
  },
  {
    label: "AI systems",
    icon: Network,
    skills: [
      "Machine learning",
      "Deep learning",
      "RAG",
      "GraphRAG",
      "Vector retrieval",
      "Hybrid search",
    ],
  },
  {
    label: "Infrastructure",
    icon: Workflow,
    skills: [
      "Neo4j",
      "Qdrant",
      "Docker",
      "AWS",
      "Agentic AI",
      "AI evaluation",
      "Reliability",
    ],
  },
];

export default function Home() {
  return (
    <main className="portfolio">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>

      <header className="site-header">
        <div className="page-width nav-layout">
          <a
            className="wordmark"
            href="#main-content"
            aria-label="Jeremiah Afari Opoku-Acheampong, home"
          >
            <span className="wordmark-mark">JAO</span>
            <span className="wordmark-caption">AI SYSTEMS</span>
          </a>
          <nav className="primary-nav" aria-label="Main navigation">
            <a href="#about">About</a>
            <a href="#work">Selected work</a>
            <a href="#expertise">Expertise</a>
            <a href="#digital-twin">Digital Twin</a>
          </nav>
          <a className="nav-contact" href="#contact">
            Get in touch <ArrowUpRight aria-hidden="true" size={15} />
          </a>
        </div>
      </header>

      <div className="page-width">
        <section
          className="hero"
          id="main-content"
          aria-labelledby="hero-title"
        >
          <div className="hero-copy reveal">
            <p className="eyebrow">
              <span className="eyebrow-line" /> SOFTWARE / AI / SYSTEMS
            </p>
            <h1 id="hero-title">
              Engineering useful intelligence, from data to decision.
            </h1>
            <p className="hero-positioning">
              Software Engineer <span>/</span> AI/ML Engineer <span>/</span> AI
              Systems Architecture
            </p>
            <p className="hero-summary">
              I work across applied AI, retrieval, and software engineering,
              with a focus on practical systems and clear technical foundations.
            </p>
            <div className="hero-actions">
              <a className="button button-primary" href="#work">
                Explore selected work{" "}
                <ArrowDownRight aria-hidden="true" size={17} />
              </a>
              <a className="button button-quiet" href="#digital-twin">
                Meet my Digital Twin{" "}
                <ArrowDownRight aria-hidden="true" size={17} />
              </a>
            </div>
            <div className="hero-education">
              <span className="micro-label">EDUCATION</span>
              <span>M.S. Data Science &amp; Business Analytics</span>
              <span className="hero-education-school">
                Wayne State University
              </span>
            </div>
          </div>

          <aside
            className="hero-visual reveal reveal-later"
            aria-label="Profile"
          >
            <div className="portrait-frame">
              <div className="portrait-orbit portrait-orbit-one" />
              <div className="portrait-orbit portrait-orbit-two" />
              <Image
                className="profile-portrait"
                src="https://github.com/Uzu-mak.png"
                alt="Jeremiah Afari Opoku-Acheampong"
                width={360}
                height={360}
                unoptimized
                priority
              />
              <span className="portrait-index">PROFILE / 01</span>
            </div>
            <div className="visual-caption">
              <span>Jeremiah Afari Opoku-Acheampong</span>
              <span className="visual-caption-location">
                Software · AI · Architecture
              </span>
            </div>
          </aside>
        </section>

        <section
          className="content-section about-section"
          id="about"
          aria-labelledby="about-title"
        >
          <div className="section-heading">
            <p className="eyebrow">01 / ABOUT</p>
            <h2 id="about-title">A systems-minded approach to applied AI.</h2>
          </div>
          <div className="about-layout">
            <p className="about-lead">
              I am a software and AI/ML engineer with an M.S. in Data Science
              and Business Analytics from Wayne State University. My interests
              sit where data, reliable backend systems, and useful AI meet.
            </p>
            <div className="about-detail">
              <p>
                My current role is a graduate practicum in manufacturing AI
                decision support at IntelliAware. I am especially interested in
                retrieval and agent systems, AI architecture, and engineering
                decisions that make complex systems more dependable.
              </p>
              <dl className="profile-facts">
                <div>
                  <dt>Based in</dt>
                  <dd>Auburn, Washington</dd>
                </div>
                <div>
                  <dt>Education</dt>
                  <dd>Wayne State University</dd>
                </div>
              </dl>
            </div>
          </div>
        </section>

        <section
          className="content-section work-section"
          id="work"
          aria-labelledby="work-title"
        >
          <div className="section-heading section-heading-inline">
            <div>
              <p className="eyebrow">02 / SELECTED WORK</p>
              <h2 id="work-title">
                Building at the intersection of AI and operations.
              </h2>
            </div>
            <a
              className="text-link"
              href="https://github.com/Uzu-mak"
              target="_blank"
              rel="noreferrer"
            >
              Browse GitHub profile{" "}
              <ArrowUpRight aria-hidden="true" size={16} />
            </a>
          </div>
          <div className="project-grid">
            <article className="project-entry project-featured">
              <div className="project-topline">
                <span className="project-number">01</span>
                <span className="project-type">GRAPH / RETRIEVAL</span>
              </div>
              <h3>
                <a
                  className="project-title-link"
                  href="https://github.com/Uzu-mak/AegisOps-AI-Multimodal-Enterprise-GraphRAG-Reliability-Platform"
                  target="_blank"
                  rel="noreferrer"
                >
                  AegisOps AI <ArrowUpRight aria-hidden="true" size={15} />
                </a>
              </h3>
              <p>
                Production-focused operational memory and GraphRAG for
                infrastructure and industrial asset operations.
              </p>
              <div className="project-stack">
                <span>PostgreSQL</span>
                <span>Qdrant</span>
                <span>Neo4j</span>
                <span>FastAPI</span>
              </div>
            </article>
            <article className="project-entry">
              <div className="project-topline">
                <span className="project-number">02</span>
                <span className="project-type">HEALTHCARE / AI ASSISTANT</span>
              </div>
              <h3>
                <a
                  className="project-title-link"
                  href="https://github.com/Uzu-mak/Consultinp-app"
                  target="_blank"
                  rel="noreferrer"
                >
                  MediNotes Pro <ArrowUpRight aria-hidden="true" size={15} />
                </a>
              </h3>
              <p>
                A full-stack AI healthcare consultation assistant for turning
                visit notes into summaries, next steps, and follow-up drafts.
              </p>
              <div className="project-stack">
                <span>Next.js</span>
                <span>FastAPI</span>
                <span>OpenAI</span>
                <span>AWS Lambda</span>
              </div>
            </article>
            <article className="project-entry">
              <div className="project-topline">
                <span className="project-number">03</span>
                <span className="project-type">
                  CAREER / WORKFLOW AUTOMATION
                </span>
              </div>
              <h3>
                <a
                  className="project-title-link"
                  href="https://github.com/Uzu-mak/CareerOps"
                  target="_blank"
                  rel="noreferrer"
                >
                  CareerOps <ArrowUpRight aria-hidden="true" size={15} />
                </a>
              </h3>
              <p>
                An AI-powered career operating system for job discovery,
                evidence-grounded application tailoring, and verified submission
                tracking.
              </p>
              <div className="project-stack">
                <span>Python</span>
                <span>Browser automation</span>
                <span>Evidence workflows</span>
              </div>
            </article>
          </div>
        </section>

        <section
          className="content-section project-directory-section"
          id="projects"
          aria-labelledby="project-directory-title"
        >
          <div className="section-heading section-heading-inline">
            <div>
              <p className="eyebrow">03 / PUBLIC REPOSITORIES</p>
              <h2 id="project-directory-title">A wider view of my work.</h2>
            </div>
            <a
              className="text-link"
              href="https://github.com/Uzu-mak"
              target="_blank"
              rel="noreferrer"
            >
              GitHub profile <ArrowUpRight aria-hidden="true" size={16} />
            </a>
          </div>
          <ProjectDirectory />
        </section>

        <section
          className="content-section expertise-section"
          id="expertise"
          aria-labelledby="expertise-title"
        >
          <div className="section-heading">
            <p className="eyebrow">04 / TECHNICAL EXPERTISE</p>
            <h2 id="expertise-title">Tools and ideas I work with.</h2>
          </div>
          <div className="skill-groups">
            {skillGroups.map(({ label, icon: Icon, skills }, index) => (
              <article className="skill-group" key={label}>
                <div className="skill-group-heading">
                  <span className="skill-index">0{index + 1}</span>
                  <Icon aria-hidden="true" size={18} strokeWidth={1.7} />
                  <h3>{label}</h3>
                </div>
                <ul className="skill-list">
                  {skills.map((skill) => (
                    <li key={skill}>{skill}</li>
                  ))}
                </ul>
              </article>
            ))}
          </div>
        </section>

        <section
          className="content-section twin-section-wrap"
          id="digital-twin"
          aria-labelledby="twin-title"
        >
          <div className="section-heading twin-intro">
            <p className="eyebrow">05 / CONVERSATION</p>
            <h2 id="twin-title">Ask my Digital Twin.</h2>
            <p>
              Explore my background, areas of focus, and the work documented
              here.
            </p>
          </div>
          <Twin />
        </section>

        <section
          className="contact-section"
          id="contact"
          aria-labelledby="contact-title"
        >
          <div>
            <p className="eyebrow">06 / CONTACT</p>
            <h2 id="contact-title">Let’s talk about thoughtful engineering.</h2>
          </div>
          <div className="contact-links">
            <a href="mailto:jeremiahafari70@gmail.com">
              <Mail aria-hidden="true" size={17} /> Email{" "}
              <ArrowUpRight aria-hidden="true" size={15} />
            </a>
            <a
              href="https://linkedin.com/in/jeremiah-opoku-acheampong-b99b40353"
              target="_blank"
              rel="noreferrer"
            >
              <ArrowUpRight aria-hidden="true" size={17} /> LinkedIn
            </a>
            <a
              href="https://github.com/Uzu-mak"
              target="_blank"
              rel="noreferrer"
            >
              <ArrowUpRight aria-hidden="true" size={17} /> GitHub
            </a>
          </div>
        </section>

        <footer className="site-footer">
          <span>Jeremiah Afari Opoku-Acheampong</span>
          <span>
            Software Engineer · AI/ML Engineer · AI Systems Architecture
          </span>
        </footer>
      </div>
    </main>
  );
}
