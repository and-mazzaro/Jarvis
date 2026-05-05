/**
 * main.js — Three.js particle orb + WebSocket state machine + UI wiring
 *
 * Orb states:
 *   idle       – slow rotation, clustered, dim blue (#1a2a4a)
 *   listening  – expand outward in pulses, cyan (#00d4ff)
 *   transcribing / retrieving / generating – fast orbit, amber (#ffaa00)
 *   speaking   – wave/ripple outward, white (#ffffff)
 */

'use strict';

// ─── Constants ────────────────────────────────────────────────────────────────

const WS_URL  = 'ws://localhost:8765';
const API_URL = 'http://localhost:3000';
const PARTICLE_COUNT = 4000;

// State colour map
const STATE_COLOUR = {
  idle:        new THREE.Color('#1a3a5c'),
  listening:   new THREE.Color('#00d4ff'),
  transcribing:new THREE.Color('#ffaa00'),
  retrieving:  new THREE.Color('#ffaa00'),
  generating:  new THREE.Color('#ffaa00'),
  speaking:    new THREE.Color('#ffffff'),
};

const STATE_LABEL_MAP = {
  idle:        'IN ATTESA',
  listening:   'IN ASCOLTO',
  transcribing:'TRASCRIZIONE',
  retrieving:  'RICERCA',
  generating:  'IN ELABORAZIONE',
  speaking:    'PARLO',
};

// ─── State ────────────────────────────────────────────────────────────────────

let currentState = 'idle';
let targetColour  = STATE_COLOUR.idle.clone();
let currentColour = STATE_COLOUR.idle.clone();

// ─── Scene setup ─────────────────────────────────────────────────────────────

const canvas   = document.getElementById('three-canvas');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setClearColor(0x000000, 0);

const scene  = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(60, 1, 0.1, 100);
camera.position.z = 4;

// ─── Particle geometry ────────────────────────────────────────────────────────

const geometry = new THREE.BufferGeometry();

// Base positions on a sphere
const basePos    = new Float32Array(PARTICLE_COUNT * 3);
const positions  = new Float32Array(PARTICLE_COUNT * 3);
const randoms    = new Float32Array(PARTICLE_COUNT);        // per-particle noise seed

for (let i = 0; i < PARTICLE_COUNT; i++) {
  const theta = Math.random() * Math.PI * 2;
  const phi   = Math.acos(2 * Math.random() - 1);
  const r     = 0.8 + Math.random() * 0.2;                 // base radius

  basePos[i * 3]     = r * Math.sin(phi) * Math.cos(theta);
  basePos[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
  basePos[i * 3 + 2] = r * Math.cos(phi);

  positions[i * 3]     = basePos[i * 3];
  positions[i * 3 + 1] = basePos[i * 3 + 1];
  positions[i * 3 + 2] = basePos[i * 3 + 2];

  randoms[i] = Math.random();
}

geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));

const material = new THREE.PointsMaterial({
  size:        0.025,
  sizeAttenuation: true,
  color:       currentColour,
  transparent: true,
  opacity:     0.85,
  depthWrite:  false,
});

const particles = new THREE.Points(geometry, material);
scene.add(particles);

// ─── Helpers ─────────────────────────────────────────────────────────────────

function lerp(a, b, t) { return a + (b - a) * t; }

function resizeRenderer() {
  const w = canvas.parentElement.clientWidth;
  const h = canvas.parentElement.clientHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}

// ─── Animation loop ───────────────────────────────────────────────────────────

let clock = new THREE.Clock();

function animate() {
  requestAnimationFrame(animate);
  const t   = clock.getElapsedTime();
  const pos = geometry.attributes.position.array;

  // Lerp colour
  currentColour.lerp(targetColour, 0.04);
  material.color.copy(currentColour);

  for (let i = 0; i < PARTICLE_COUNT; i++) {
    const bx = basePos[i * 3];
    const by = basePos[i * 3 + 1];
    const bz = basePos[i * 3 + 2];
    const rnd = randoms[i];

    let px = bx, py = by, pz = bz;

    switch (currentState) {
      case 'idle': {
        const breathe = 1.0 + 0.06 * Math.sin(t * 0.4 + rnd * Math.PI * 2);
        px = bx * breathe;
        py = by * breathe;
        pz = bz * breathe;
        break;
      }
      case 'listening': {
        const pulse = 1.0 + 0.3 * Math.abs(Math.sin(t * 2.5 + rnd * Math.PI));
        px = bx * pulse;
        py = by * pulse;
        pz = bz * pulse;
        break;
      }
      case 'transcribing':
      case 'retrieving':
      case 'generating': {
        const speed = 4.0 + rnd * 2.0;
        const orbitR = 0.15 + rnd * 0.2;
        px = bx + orbitR * Math.cos(t * speed + rnd * 12);
        py = by + orbitR * Math.sin(t * speed * 1.2 + rnd * 8);
        pz = bz + orbitR * Math.sin(t * speed * 0.7 + rnd * 6);
        break;
      }
      case 'speaking': {
        const dist = Math.sqrt(bx * bx + by * by + bz * bz);
        const wave = 0.25 * Math.sin(dist * 8 - t * 6 + rnd * 5);
        px = bx * (1.0 + wave);
        py = by * (1.0 + wave);
        pz = bz * (1.0 + wave);
        break;
      }
    }

    pos[i * 3]     = px;
    pos[i * 3 + 1] = py;
    pos[i * 3 + 2] = pz;
  }

  geometry.attributes.position.needsUpdate = true;

  // Organically complex rotation
  const rotFactor = currentState === 'idle' ? 0.05 : 0.2;
  particles.rotation.y += rotFactor * 0.016 + Math.sin(t * 0.2) * 0.001;
  particles.rotation.x += rotFactor * 0.008 + Math.cos(t * 0.3) * 0.001;

  renderer.render(scene, camera);
}

window.addEventListener('resize', resizeRenderer);
resizeRenderer();
animate();

// ─── Window Controls ─────────────────────────────────────────────────────────

if (window.jarvisIPC) {
  document.getElementById('win-min').addEventListener('click', () => window.jarvisIPC.minimize());
  document.getElementById('win-max').addEventListener('click', () => window.jarvisIPC.maximize());
  document.getElementById('win-close').addEventListener('click', () => window.jarvisIPC.close());
}


// ─── WebSocket ────────────────────────────────────────────────────────────────

const stateLabel    = document.getElementById('state-label');
const transcriptEl  = document.getElementById('transcript-text');
const dotWs         = document.getElementById('dot-ws');

let ws = null;
let wsReconnectTimer = null;

function connectWS() {
  if (ws && ws.readyState === WebSocket.OPEN) return;
  ws = new WebSocket(WS_URL);

  ws.addEventListener('open', () => {
    dotWs.className = 'status-dot online';
    clearTimeout(wsReconnectTimer);
  });

  ws.addEventListener('message', (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      applyState(msg.state, msg);
    } catch (_) {}
  });

  ws.addEventListener('close', () => {
    dotWs.className = 'status-dot offline';
    wsReconnectTimer = setTimeout(connectWS, 2000);
  });

  ws.addEventListener('error', () => { ws.close(); });
}

function applyState(state, msg = {}) {
  currentState = state;
  targetColour = (STATE_COLOUR[state] || STATE_COLOUR.idle).clone();

  const label = STATE_LABEL_MAP[state] || state.toUpperCase();
  stateLabel.textContent = label;
  stateLabel.className   = '';
  if (state === 'listening')  stateLabel.classList.add('listening');
  if (state === 'generating') stateLabel.classList.add('generating');
  if (state === 'speaking')   stateLabel.classList.add('speaking');

  if (msg.partial) {
    transcriptEl.textContent = msg.partial;
    transcriptEl.scrollTop = transcriptEl.scrollHeight;
  }
  if (state === 'listening') {
    transcriptEl.textContent = '';
  }
}

// ─── Sidebar navigation ───────────────────────────────────────────────────────

const btnOrb       = document.getElementById('btn-orb');
const btnKnowledge = document.getElementById('btn-knowledge');
const orbView      = document.getElementById('orb-view');
const knowledgeView= document.getElementById('knowledge-view');

btnOrb.addEventListener('click', () => {
  orbView.classList.remove('hidden');
  knowledgeView.classList.add('hidden');
  btnOrb.classList.add('active');
  btnKnowledge.classList.remove('active');
});

btnKnowledge.addEventListener('click', () => {
  knowledgeView.classList.remove('hidden');
  orbView.classList.add('hidden');
  btnKnowledge.classList.add('active');
  btnOrb.classList.remove('active');
  window.knowledgePanel && window.knowledgePanel.refresh();
});

// Settings stub
document.getElementById('btn-settings').addEventListener('click', () => {
  showToast('Il pannello impostazioni sarà disponibile in un futuro aggiornamento.', 'info');
});

// ─── Status polling ───────────────────────────────────────────────────────────

async function pollStatus() {
  try {
    const r = await fetch(`${API_URL}/api/status`);
    if (r.ok) {
      dotWs.className = 'status-dot online';
      hideLoading();
    }
  } catch (_) {}
}

// ─── Toast notifications ──────────────────────────────────────────────────────

const toastContainer = document.getElementById('toast-container');

function showToast(message, type = 'info', duration = 3500) {
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = message;
  toastContainer.appendChild(el);
  setTimeout(() => {
    el.style.opacity = '0';
    el.style.transition = 'opacity 0.3s';
    setTimeout(() => el.remove(), 300);
  }, duration);
}
window.showToast = showToast;

// ─── Loading overlay ──────────────────────────────────────────────────────────

const loadingOverlay = document.getElementById('loading-overlay');
const loadingMessage = document.getElementById('loading-message');
let loadingHidden = false;

function hideLoading() {
  if (loadingHidden) return;
  loadingHidden = true;
  loadingOverlay.classList.add('hidden');
  setTimeout(() => { loadingOverlay.style.display = 'none'; }, 700);
}

// Update loading message with animated dots
let msgPhase = 0;
const loadingPhrases = [
  'INIZIALIZZAZIONE',
  'CONNESSIONE AL BACKEND',
  'CARICAMENTO MODELLI',
  'PREPARAZIONE CONOSCENZA',
  'QUASI PRONTO',
];
setInterval(() => {
  loadingMessage.textContent = loadingPhrases[msgPhase % loadingPhrases.length];
  msgPhase++;
}, 1200);

// Poll backend until ready (max 30 seconds)
let pollCount = 0;
const pollTimer = setInterval(async () => {
  pollCount++;
  await pollStatus();
  if (loadingHidden || pollCount > 60) clearInterval(pollTimer);
}, 500);

// ─── Init ────────────────────────────────────────────────────────────────────

connectWS();
setInterval(pollStatus, 5000);
