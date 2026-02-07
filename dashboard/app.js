// Crowd Agent Dashboard
// Fetches data from the GitHub API (unauthenticated) and renders the dashboard.

const REPO = 'trevorstenson/crowd-agent';
const API = 'https://api.github.com';

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`GitHub API error: ${res.status}`);
  return res.json();
}

// --- Build Log Manager ---

const BuildLogManager = {
  logFile: 'agent/build-log.json',

  async fetchLogs() {
    try {
      const response = await fetch(this.logFile);
      if (!response.ok) throw new Error('Failed to fetch logs');
      return await response.json();
    } catch (error) {
      console.error('Error fetching build logs:', error);
      return { builds: [] };
    }
  },

  async displayLogs(filters = {}) {
    const data = await this.fetchLogs();
    let builds = data.builds || [];

    // Apply filters
    if (filters.status) {
      builds = builds.filter(b => b.status === filters.status);
    }

    // Limit results
    const limit = filters.limit || 10;
    builds = builds.slice(0, limit);

    // Render logs
    this.renderLogs(builds);
  },

  renderLogs(builds) {
    const container = document.getElementById('build-log-container');
    const emptyState = document.getElementById('build-log-empty');

    if (builds.length === 0) {
      container.style.display = 'none';
      emptyState.style.display = 'block';
      return;
    }

    container.style.display = 'flex';
    emptyState.style.display = 'none';

    container.innerHTML = builds.map(build => `
      <div class="build-log-entry status-${build.status}">
        <div class="build-header">
          <span class="build-date">${new Date(build.timestamp).toLocaleString()}</span>
          <span class="build-status ${build.status}">${build.status.toUpperCase()}</span>
        </div>
        <div class="build-issue">${build.issue}</div>
        <div class="build-stats">
          <span class="stat">
            📄 ${build.files_changed.length} files changed
          </span>
          <span class="stat">
            ⏱️ ${this.formatDuration(build.duration_seconds)}
          </span>
        </div>
        <div class="build-summary">${build.summary}</div>
        <button class="btn-details" onclick="BuildLogManager.showDetails('${build.id}')">
          View Details
        </button>
      </div>
    `).join('');
  },

  formatDuration(seconds) {
    if (seconds < 60) return `${seconds}s`;
    const minutes = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${minutes}m ${secs}s`;
  },

  async showDetails(buildId) {
    const data = await this.fetchLogs();
    const build = data.builds.find(b => b.id === buildId);

    if (!build) return;

    const modal = document.getElementById('build-detail-modal');
    const content = document.getElementById('build-detail-content');

    content.innerHTML = `
      <h3>${build.issue}</h3>
      <div class="detail-grid">
        <div><strong>Date:</strong> ${new Date(build.timestamp).toLocaleString()}</div>
        <div><strong>Status:</strong> <span class="build-status ${build.status}">${build.status.toUpperCase()}</span></div>
        <div><strong>Duration:</strong> ${this.formatDuration(build.duration_seconds)}</div>
        <div><strong>Commit:</strong> <code>${build.commit_hash}</code></div>
      </div>
      <div class="detail-section">
        <h4>Files Changed</h4>
        <ul>
          ${build.files_changed.map(f => `<li>${f}</li>`).join('')}
        </ul>
      </div>
      <div class="detail-section">
        <h4>Summary</h4>
        <p>${build.summary}</p>
      </div>
      ${build.error_message ? `
        <div class="detail-section error">
          <h4>Error</h4>
          <pre>${this.escapeHtml(build.error_message)}</pre>
        </div>
      ` : ''}
    `;

    modal.style.display = 'block';
  },

  escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  },

  setupEventListeners() {
    const statusFilter = document.getElementById('status-filter');
    const logLimit = document.getElementById('log-limit');
    const refreshBtn = document.getElementById('refresh-logs');
    const modal = document.getElementById('build-detail-modal');
    const closeBtn = document.querySelector('.close');

    if (statusFilter) {
      statusFilter.addEventListener('change', () => this.applyFilters());
    }

    if (logLimit) {
      logLimit.addEventListener('change', () => this.applyFilters());
    }

    if (refreshBtn) {
      refreshBtn.addEventListener('click', () => this.applyFilters());
    }

    if (closeBtn) {
      closeBtn.addEventListener('click', () => {
        modal.style.display = 'none';
      });
    }

    if (modal) {
      window.addEventListener('click', (event) => {
        if (event.target === modal) {
          modal.style.display = 'none';
        }
      });
    }
  },

  applyFilters() {
    const status = document.getElementById('status-filter')?.value || '';
    const limit = parseInt(document.getElementById('log-limit')?.value || '10');

    this.displayLogs({
      status: status || undefined,
      limit: limit,
    });
  },
};

// --- Agent Stats ---

async function loadAgentStats() {
  try {
    const data = await fetchJSON(`${API}/repos/${REPO}/contents/agent/memory.json`);
    const memory = JSON.parse(atob(data.content));

    document.getElementById('total-builds').textContent = memory.total_builds;

    if (memory.total_builds > 0) {
      const rate = Math.round((memory.successful_builds / memory.total_builds) * 100);
      document.getElementById('success-rate').textContent = `${rate}%`;
    } else {
      document.getElementById('success-rate').textContent = 'N/A';
    }

    document.getElementById('streak').textContent = memory.streak;

    if (memory.last_build_date) {
      const date = new Date(memory.last_build_date);
      document.getElementById('last-build').textContent = date.toLocaleDateString();
    } else {
      document.getElementById('last-build').textContent = 'Never';
    }
  } catch (e) {
    console.warn('Could not load agent stats:', e);
    document.getElementById('total-builds').textContent = '0';
    document.getElementById('success-rate').textContent = 'N/A';
    document.getElementById('streak').textContent = '0';
    document.getElementById('last-build').textContent = 'Never';
  }
}

// --- Issues ---

function createIssueItem(issue, showVotes) {
  const a = document.createElement('a');
  a.href = issue.html_url;
  a.target = '_blank';
  a.className = 'issue-item';

  if (showVotes) {
    const up = issue.reactions?.['+1'] || 0;
    const down = issue.reactions?.['-1'] || 0;
    const net = up - down;
    const voteEl = document.createElement('span');
    voteEl.className = 'vote-count' + (net < 0 ? ' vote-negative' : net === 0 ? ' vote-zero' : '');
    voteEl.textContent = (net > 0 ? '\u25B2 ' : net < 0 ? '\u25BC ' : '- ') + Math.abs(net);
    a.appendChild(voteEl);
  }

  const titleEl = document.createElement('span');
  titleEl.className = 'issue-title';
  titleEl.textContent = issue.title;
  a.appendChild(titleEl);

  const numEl = document.createElement('span');
  numEl.className = 'issue-number';
  numEl.textContent = `#${issue.number}`;
  a.appendChild(numEl);

  return a;
}

async function loadVotingIssues() {
  const container = document.getElementById('votes-list');
  try {
    const issues = await fetchJSON(
      `${API}/repos/${REPO}/issues?labels=voting&state=open&sort=reactions-%2B1&direction=desc&per_page=20`
    );

    container.innerHTML = '';
    if (issues.length === 0) {
      container.innerHTML = '<p class="empty-state">No issues up for vote right now. Submit one!</p>';
      return;
    }

    // Sort by net votes (upvotes minus downvotes)
    issues.sort((a, b) => {
      const netA = (a.reactions?.['+1'] || 0) - (a.reactions?.['-1'] || 0);
      const netB = (b.reactions?.['+1'] || 0) - (b.reactions?.['-1'] || 0);
      return netB - netA;
    });

    for (const issue of issues) {
      container.appendChild(createIssueItem(issue, true));
    }
  } catch (e) {
    console.warn('Could not load voting issues:', e);
    container.innerHTML = '<p class="empty-state">Could not load issues. Try refreshing.</p>';
  }
}

async function loadBuildingIssues() {
  const section = document.getElementById('now-building');
  const container = document.getElementById('building-content');
  try {
    const issues = await fetchJSON(
      `${API}/repos/${REPO}/issues?labels=building&state=open&per_page=5`
    );

    if (issues.length === 0) {
      section.classList.add('hidden');
      return;
    }

    section.classList.remove('hidden');
    container.innerHTML = '';
    for (const issue of issues) {
      container.appendChild(createIssueItem(issue, false));
    }
  } catch (e) {
    console.warn('Could not load building issues:', e);
    section.classList.add('hidden');
  }
}

async function loadRecentBuilds() {
  const container = document.getElementById('builds-list');
  try {
    const issues = await fetchJSON(
      `${API}/repos/${REPO}/issues?labels=shipped&state=closed&per_page=10&sort=updated&direction=desc`
    );

    container.innerHTML = '';
    if (issues.length === 0) {
      container.innerHTML = '<p class="empty-state">No builds shipped yet. The first one is coming soon.</p>';
      return;
    }

    for (const issue of issues) {
      const item = createIssueItem(issue, false);
      const label = document.createElement('span');
      label.className = 'label-shipped';
      label.textContent = 'SHIPPED';
      item.appendChild(label);
      container.appendChild(item);
    }
  } catch (e) {
    console.warn('Could not load recent builds:', e);
    container.innerHTML = '<p class="empty-state">Could not load builds. Try refreshing.</p>';
  }
}

// --- Source Code ---

async function loadSourceCode() {
  const codeEl = document.getElementById('source-code');
  try {
    const data = await fetchJSON(`${API}/repos/${REPO}/contents/agent/main.py`);
    const source = atob(data.content);
    codeEl.textContent = source;
    codeEl.classList.remove('loading');
  } catch (e) {
    console.warn('Could not load source code:', e);
    codeEl.textContent = 'Could not load source code.';
    codeEl.classList.remove('loading');
  }
}

// --- Changelog ---

async function loadChangelog() {
  const container = document.getElementById('changelog-content');
  try {
    const data = await fetchJSON(`${API}/repos/${REPO}/contents/CHANGELOG.md`);
    const markdown = atob(data.content);

    // Split into entries on "---" separators, skip the header
    const sections = markdown.split(/\n---\n/).filter(s => s.trim());
    // First section is the header ("# Crowd Agent Changelog\n\n..."), skip it
    const entries = sections.slice(1);

    container.innerHTML = '';
    if (entries.length === 0) {
      container.innerHTML = '<p class="empty-state">No builds yet. The first changelog entry is coming soon.</p>';
      return;
    }

    for (const entry of entries) {
      const lines = entry.trim().split('\n').filter(l => l.trim());
      if (lines.length === 0) continue;

      const el = document.createElement('div');

      // Parse heading: "## [+] #1 — Title" or "## [x] #1 — Title"
      const headingMatch = lines[0]?.match(/^##\s*\[([+x])\]\s*(.*)/);
      const metaLine = lines[1] || '';
      const bodyLines = lines.slice(2);

      const success = headingMatch ? headingMatch[1] === '+' : true;
      const title = headingMatch ? headingMatch[2] : lines[0].replace(/^#+\s*/, '');

      el.className = `changelog-entry ${success ? 'changelog-success' : 'changelog-failure'}`;

      const statusIcon = document.createElement('span');
      statusIcon.className = 'changelog-status';
      statusIcon.textContent = success ? '+' : 'x';
      el.appendChild(statusIcon);

      const content = document.createElement('div');
      content.className = 'changelog-body';

      const titleEl = document.createElement('div');
      titleEl.className = 'changelog-title';
      titleEl.textContent = title;
      content.appendChild(titleEl);

      if (metaLine) {
        const metaEl = document.createElement('div');
        metaEl.className = 'changelog-meta';
        metaEl.textContent = metaLine.replace(/\*\*/g, '');
        content.appendChild(metaEl);
      }

      if (bodyLines.length > 0) {
        const textEl = document.createElement('div');
        textEl.className = 'changelog-text';
        textEl.textContent = bodyLines.join(' ');
        content.appendChild(textEl);
      }

      el.appendChild(content);
      container.appendChild(el);
    }
  } catch (e) {
    console.warn('Could not load changelog:', e);
    container.innerHTML = '<p class="empty-state">No builds yet. The first changelog entry is coming soon.</p>';
  }
}

// --- Init ---

async function init() {
  // Run all fetches in parallel
  await Promise.allSettled([
    loadAgentStats(),
    loadVotingIssues(),
    loadBuildingIssues(),
    loadRecentBuilds(),
    loadSourceCode(),
    loadChangelog(),
    BuildLogManager.displayLogs({ limit: 10 }),
  ]);

  // Setup event listeners for build log
  BuildLogManager.setupEventListeners();
}

init();
