/**
 * knowledge-panel.js
 *
 * Handles the Knowledge Manager UI:
 *   - Drag-and-drop / file picker document ingestion
 *   - Indexed file list with delete
 *   - Wikipedia search test box
 *   - Stats counters
 */

'use strict';

const API = (window.location.origin && window.location.origin.startsWith('http')) 
  ? window.location.origin 
  : 'http://localhost:3000';

// ─── Tab switching ────────────────────────────────────────────────────────────

document.querySelectorAll('.kp-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.kp-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.kp-pane').forEach(p => p.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById(`pane-${tab.dataset.tab}`).classList.add('active');
  });
});

// ─── Document ingestion ───────────────────────────────────────────────────────

const dropZone       = document.getElementById('drop-zone');
const fileInput      = document.getElementById('file-input');
const fileList       = document.getElementById('file-list');
const progressBox    = document.getElementById('ingest-progress');
const progressText   = document.getElementById('ingest-progress-text');
const progressBar    = document.getElementById('progress-bar');

// Click to open file picker
dropZone.addEventListener('click', () => fileInput.click());

// Drag events
dropZone.addEventListener('dragover', e => {
  e.preventDefault();
  dropZone.classList.add('drag-over');
});
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  handleFiles(Array.from(e.dataTransfer.files));
});

fileInput.addEventListener('change', () => {
  handleFiles(Array.from(fileInput.files));
  fileInput.value = '';
});

async function handleFiles(files) {
  const supported = files.filter(f =>
    /\.(pdf|txt|docx)$/i.test(f.name)  // .doc rimosso: non supportato da python-docx
  );

  if (!supported.length) {
    showToast('Supportati solo file PDF, TXT e DOCX.', 'error');
    return;
  }

  progressBox.classList.add('visible');
  progressBar.style.width = '0%';

  let done = 0;
  for (const file of supported) {
    progressText.textContent = `Indicizzazione di ${file.name} ...`;
    progressBar.style.width = `${(done / supported.length) * 100}%`;

    try {
      // Usa FormData per uploadare il contenuto reale del file.
      // Funziona sia in Electron che nel browser (non dipende da file.path).
      const formData = new FormData();
      formData.append('file', file, file.name);

      const res = await fetch(`${API}/api/ingest/upload`, {
        method: 'POST',
        body: formData,
      });

      if (!res.ok) {
        const text = await res.text();
        showToast(`Errore (${res.status}): ${text}`, 'error');
      } else {
        const data = await res.json();
        if (data.error) {
          showToast(`Errore: ${data.error}`, 'error');
        } else if (data.already_indexed) {
          showToast(`${file.name} già indicizzato.`, 'info');
        } else {
          showToast(`✓ ${file.name} — ${data.chunks_added} frammenti aggiunti.`, 'success');
        }
      }
    } catch (err) {
      showToast(`Errore nell'indicizzazione di ${file.name}: ${err.message}`, 'error');
    }

    done++;
  }

  progressBar.style.width = '100%';
  progressText.textContent = `Completato — ${done} file elaborati.`;
  setTimeout(() => progressBox.classList.remove('visible'), 2000);
  await refreshFileList();
}

// ─── File list ────────────────────────────────────────────────────────────────

async function refreshFileList() {
  try {
    const res  = await fetch(`${API}/api/knowledge`);
    const data = await res.json();
    renderFileList(data);
    updateStats(data);
  } catch (_) {
    fileList.innerHTML = '<div class="empty-state">Impossibile caricare i file — backend offline?</div>';
  }
}

function renderFileList(files) {
  if (!files || !files.length) {
    fileList.innerHTML = '<div class="empty-state">Nessun documento indicizzato.</div>';
    return;
  }

  fileList.innerHTML = files.map(f => {
    const icon = getFileIcon(f.file_name);
    const date = f.date_added
      ? new Date(f.date_added).toLocaleDateString('it-IT', { month: 'short', day: 'numeric', year: 'numeric' })
      : '—';
    return `
      <div class="file-card" data-name="${escapeHtml(f.file_name)}">
        <div class="file-icon">${icon}</div>
        <div class="file-info">
          <div class="file-name" title="${escapeHtml(f.file_name)}">${escapeHtml(f.file_name)}</div>
          <div class="file-meta">${escapeHtml(String(f.chunk_count ?? 0))} frammenti · ${escapeHtml(date)}</div>
        </div>
        <button class="file-delete" data-name="${escapeHtml(f.file_name)}" title="Rimuovi dall'indice">✕</button>
      </div>`;
  }).join('');

  // Wire delete buttons
  fileList.querySelectorAll('.file-delete').forEach(btn => {
    btn.addEventListener('click', async () => {
      const name = btn.dataset.name;
      if (!confirm(`Rimuovere "${name}" dalla conoscenza?`)) return;
      try {
        const res = await fetch(`${API}/api/knowledge/delete`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ file_name: name }),
        });
        if (!res.ok) {
          const text = await res.text();
          showToast(`Eliminazione fallita (${res.status}): ${text}`, 'error');
        } else {
          showToast(`Rimosso ${name}.`, 'info');
          await refreshFileList();
        }
      } catch (err) {
        showToast(`Eliminazione fallita: ${err.message}`, 'error');
      }
    });
  });
}

function updateStats(files) {
  const totalChunks = files.reduce((s, f) => s + (f.chunk_count || 0), 0);
  document.getElementById('stat-files').textContent   = files.length;
  document.getElementById('stat-chunks').textContent  = totalChunks;

  const latest = files.reduce((acc, f) => {
    if (!f.date_added) return acc;
    return (!acc || f.date_added > acc) ? f.date_added : acc;
  }, null);
  document.getElementById('stat-recent').textContent = latest
    ? new Date(latest).toLocaleDateString('it-IT', { month: 'short', day: 'numeric' })
    : '—';
}

// ─── Wikipedia search ─────────────────────────────────────────────────────────

const wikiQuery  = document.getElementById('wiki-query');
const wikiBtn    = document.getElementById('wiki-search-btn');
const wikiStatus = document.getElementById('wiki-status');
const wikiResult = document.getElementById('wiki-result');

async function checkKiwix() {
  try {
    const res  = await fetch(`${API}/api/kiwix/status`);
    const data = await res.json();
    if (data.online) {
      wikiStatus.textContent = '● kiwix-serve online — Wikipedia pronta';
      wikiStatus.style.color = 'var(--green)';
    } else {
      wikiStatus.textContent = '⚠ kiwix-serve offline — Ricerca Wikipedia non disponibile';
      wikiStatus.style.color = 'var(--amber)';
    }
  } catch (_) {
    wikiStatus.textContent = '✗ Backend non raggiungibile';
    wikiStatus.style.color = 'var(--red)';
  }
}

async function doWikiSearch() {
  const query = wikiQuery.value.trim();
  if (!query) return;

  wikiBtn.disabled = true;
  wikiResult.textContent = 'Ricerca in corso...';

  try {
    const res = await fetch(`${API}/api/kiwix/search`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query }),
    });
    const data = await res.json();
    if (data.error) {
      wikiResult.textContent = `Errore: ${data.error}`;
    } else if (!data.excerpt) {
      wikiResult.textContent = 'Nessun articolo trovato per questa ricerca.';
    } else {
      wikiResult.textContent = data.excerpt;
    }
  } catch (err) {
    wikiResult.textContent = `Richiesta fallita: ${err.message}`;
  } finally {
    wikiBtn.disabled = false;
  }
}

wikiBtn.addEventListener('click', doWikiSearch);
wikiQuery.addEventListener('keydown', e => { if (e.key === 'Enter') doWikiSearch(); });

// ─── Helpers ──────────────────────────────────────────────────────────────────

function getFileIcon(fileName) {
  if (/\.pdf$/i.test(fileName))  return '📄';
  if (/\.docx?$/i.test(fileName)) return '📝';
  if (/\.txt$/i.test(fileName))  return '📃';
  return '📁';
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ─── Public API ───────────────────────────────────────────────────────────────

window.knowledgePanel = {
  refresh: async () => {
    await refreshFileList();
    checkKiwix();
  },
};

// ─── Init ────────────────────────────────────────────────────────────────────

refreshFileList();
checkKiwix();
