// CrowdPilot Dashboard
// Fetches data from the GitHub API (unauthenticated) and renders the dashboard.

const REPO = 'trevorstenson/crowd-agent';
const API = 'https://api.github.com';

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`GitHub API error: ${res.status}`);
  return res.json();
}

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
    const reactions = issue.reactions?.['+1'] || 0;
    const voteEl = document.createElement('span');
    voteEl.className = 'vote-count';
    voteEl.textContent = `\u{1F44D} ${reactions}`;
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

    // Sort by thumbs-up reactions
    issues.sort((a, b) => (b.reactions?.['+1'] || 0) - (a.reactions?.['+1'] || 0));

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
    // First section is the header ("# CrowdPilot Changelog\n\n..."), skip it
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
  ]);
}

init();
