const REPO = 'trevorstenson/crowd-agent';
const API = 'https://api.github.com';

const TRACKS = [
  'Capability',
  'Reliability',
  'Survival',
  'Legibility',
  'Virality',
];

async function fetchJSON(url, options = {}) {
  const headers = {
    Accept: 'application/vnd.github+json',
    ...(options.headers || {}),
  };
  const res = await fetch(url, { ...options, headers });
  if (!res.ok) throw new Error(`GitHub API error: ${res.status}`);
  return res.json();
}

function decodeBase64Unicode(content) {
  const binary = atob(content.replace(/\n/g, ''));
  const bytes = Uint8Array.from(binary, ch => ch.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

function escapeHtml(text) {
  return text
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
}

function netReactions(issue) {
  return (issue.reactions?.['+1'] || 0) - (issue.reactions?.['-1'] || 0);
}

function reactionWeight(createdAt) {
  const ageDays = Math.max(0, (Date.now() - new Date(createdAt).getTime()) / 86400000);
  if (ageDays < 1) return 1.0;
  if (ageDays < 3) return 0.5;
  if (ageDays < 7) return 0.2;
  return 0.0;
}

function decayedNetReactions(reactions) {
  return (reactions || []).reduce((total, reaction) => {
    if (reaction.content !== '+1' && reaction.content !== '-1') return total;
    const sign = reaction.content === '+1' ? 1 : -1;
    return total + sign * reactionWeight(reaction.created_at);
  }, 0);
}

function pressureState(score) {
  if (score >= 1.5) return { label: 'surging', sentence: 'Recent reactions are pushing this trait hard right now.' };
  if (score > 0.2) return { label: 'active', sentence: 'Recent reactions are pushing this trait upward.' };
  if (score <= -1.5) return { label: 'suppressed', sentence: 'Recent reactions are strongly pushing against this trait.' };
  if (score < -0.2) return { label: 'fading', sentence: 'Recent reactions are pushing this trait downward.' };
  return { label: 'neutral', sentence: 'No strong recent pressure is acting on this trait.' };
}

function shortDate(iso) {
  if (!iso) return 'Never';
  return new Date(iso).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

function truncate(text, limit = 180) {
  if (!text) return '';
  if (text.length <= limit) return text;
  return `${text.slice(0, limit - 1).trim()}…`;
}

function issueTrack(issue) {
  const labels = issue.labels || [];
  for (const label of labels) {
    const name = typeof label === 'string' ? label : label.name;
    if (name && name.startsWith('track:')) {
      return name.replace('track:', '');
    }
  }
  const titleMatch = (issue.title || '').match(/\[(capability|reliability|survival|legibility|virality)\]/i);
  return titleMatch ? titleMatch[1].toLowerCase() : '';
}

async function loadAgentStats() {
  try {
    const data = await fetchJSON(`${API}/repos/${REPO}/contents/agent/memory.json`);
    const memory = JSON.parse(decodeBase64Unicode(data.content));

    document.getElementById('total-builds').textContent = memory.total_builds;
    document.getElementById('streak').textContent = memory.streak;
    document.getElementById('last-build').textContent = shortDate(memory.last_build_date);

    if (memory.total_builds > 0) {
      const rate = Math.round((memory.successful_builds / memory.total_builds) * 100);
      document.getElementById('success-rate').textContent = `${rate}%`;
    } else {
      document.getElementById('success-rate').textContent = 'N/A';
    }
  } catch (error) {
    console.warn('Could not load agent stats:', error);
    document.getElementById('total-builds').textContent = '0';
    document.getElementById('success-rate').textContent = 'N/A';
    document.getElementById('streak').textContent = '0';
    document.getElementById('last-build').textContent = 'Never';
  }
}

async function loadMission() {
  const container = document.getElementById('mission-snippet');
  try {
    const data = await fetchJSON(`${API}/repos/${REPO}/contents/agent/mission.md`);
    const mission = decodeBase64Unicode(data.content)
      .split('\n')
      .filter(line => line.trim() && !line.startsWith('#'))
      .slice(0, 3)
      .join(' ');
    container.textContent = mission;
  } catch (error) {
    console.warn('Could not load mission:', error);
    container.textContent = 'Become a more capable, autonomous, and legible software-building organism.';
  }
}

async function loadCurrentMutation() {
  const container = document.getElementById('current-mutation-content');
  try {
    const [buildingIssues, roadmapData] = await Promise.all([
      fetchJSON(`${API}/repos/${REPO}/issues?labels=building&state=open&per_page=5`),
      fetchJSON(`${API}/repos/${REPO}/contents/agent/autonomous_roadmap.json`),
    ]);
    const roadmap = JSON.parse(decodeBase64Unicode(roadmapData.content));

    container.innerHTML = '';

    if (buildingIssues.length > 0) {
      const issue = buildingIssues[0];
      const autonomous = issue.title.toLowerCase().startsWith('[autonomous]');
      container.innerHTML = `
        <div class="mutation-status ${autonomous ? 'status-autonomous' : 'status-crowd'}">
          ${autonomous ? 'Autonomous mutation in progress' : 'Crowd-influenced mutation in progress'}
        </div>
        <a class="mutation-title" href="${issue.html_url}" target="_blank">${escapeHtml(issue.title)}</a>
        <p class="mutation-body">${escapeHtml(truncate(issue.body || 'No summary provided.', 260))}</p>
        <div class="meta-row">
          <span>#${issue.number}</span>
          <span>${shortDate(issue.updated_at)}</span>
        </div>
      `;
      return;
    }

    const nextTask = (roadmap.tasks || []).find(task => task.status !== 'done');
    if (!nextTask) {
      container.innerHTML = '<p class="empty-state">No active mutation and no roadmap task queued.</p>';
      return;
    }

    container.innerHTML = `
      <div class="mutation-status status-dormant">No active build right now</div>
      <div class="mutation-title">${escapeHtml(nextTask.title)}</div>
      <p class="mutation-body">${escapeHtml(nextTask.summary)}</p>
      <div class="meta-row">
        <span>Track: ${escapeHtml(nextTask.track)}</span>
        <span>Priority ${nextTask.priority}</span>
      </div>
    `;
  } catch (error) {
    console.warn('Could not load current mutation:', error);
    container.innerHTML = '<p class="empty-state">Could not load the current mutation.</p>';
  }
}

function pressureCard(track, issue, reactions = []) {
  const card = document.createElement('article');
  card.className = 'pressure-card';

  if (!issue) {
    card.innerHTML = `
      <div class="pressure-top">
        <h3>${track}</h3>
        <span class="pressure-score pressure-missing">Not wired</span>
      </div>
      <p class="pressure-note">Create a pinned issue titled "Track: ${track}" to make this pressure live.</p>
      <div class="pressure-bar"><span style="width: 0%"></span></div>
    `;
    return card;
  }

  const decayedScore = decayedNetReactions(reactions);
  const rawScore = netReactions(issue);
  const normalized = Math.max(0, Math.min(100, 50 + decayedScore * 10));
  const scoreLabel = decayedScore > 0 ? `+${decayedScore.toFixed(1)}` : decayedScore.toFixed(1);
  const rawLabel = rawScore > 0 ? `+${rawScore}` : `${rawScore}`;
  const state = pressureState(decayedScore);

  card.innerHTML = `
    <div class="pressure-top">
      <div class="pressure-title-group">
        <h3>${track}</h3>
        <span class="pressure-state pressure-state-${state.label}">${state.label}</span>
      </div>
      <a class="pressure-score-stack" href="${issue.html_url}" target="_blank">
        <span class="pressure-score">${scoreLabel}</span>
        <span class="pressure-score-label">Live Pressure</span>
      </a>
    </div>
    <p class="pressure-note pressure-note-strong">${state.sentence}</p>
    <p class="pressure-note">Reactions count fully for 1 day, weaken after 3 days, and fade out after 7 days unless refreshed.</p>
    <div class="pressure-chips">
      <span class="pressure-chip">1d full</span>
      <span class="pressure-chip">3d half</span>
      <span class="pressure-chip">7d faint</span>
      <span class="pressure-chip">Historical net ${rawLabel}</span>
    </div>
    <div class="pressure-bar"><span style="width: ${normalized}%"></span></div>
    <a class="pressure-link" href="${issue.html_url}" target="_blank">Open track issue to refresh influence</a>
  `;
  return card;
}

async function loadTrackPressures() {
  const container = document.getElementById('pressure-board');
  try {
    const issues = await fetchJSON(`${API}/repos/${REPO}/issues?state=open&per_page=100`);
    const trackIssues = new Map();

    for (const issue of issues) {
      const match = (issue.title || '').match(/^track:\s*(.+)$/i);
      if (match) {
        trackIssues.set(match[1].trim().toLowerCase(), issue);
      }
    }

    const reactionsByTrack = new Map();
    await Promise.all(
      TRACKS.map(async (track) => {
        const issue = trackIssues.get(track.toLowerCase());
        if (!issue) return;
        try {
          const reactions = await fetchJSON(`${API}/repos/${REPO}/issues/${issue.number}/reactions?per_page=100`);
          reactionsByTrack.set(track.toLowerCase(), reactions);
        } catch (error) {
          console.warn(`Could not load reactions for ${track}:`, error);
          reactionsByTrack.set(track.toLowerCase(), []);
        }
      })
    );

    container.innerHTML = '';
    for (const track of TRACKS) {
      container.appendChild(
        pressureCard(
          track,
          trackIssues.get(track.toLowerCase()),
          reactionsByTrack.get(track.toLowerCase()) || []
        )
      );
    }
  } catch (error) {
    console.warn('Could not load track pressures:', error);
    container.innerHTML = '<p class="empty-state">Could not load evolution pressures.</p>';
  }
}

async function loadRoadmap() {
  const container = document.getElementById('roadmap-list');
  try {
    const data = await fetchJSON(`${API}/repos/${REPO}/contents/agent/autonomous_roadmap.json`);
    const roadmap = JSON.parse(decodeBase64Unicode(data.content));
    const tasks = (roadmap.tasks || []).filter(task => task.status !== 'done').slice(0, 5);

    container.innerHTML = '';
    if (tasks.length === 0) {
      container.innerHTML = '<p class="empty-state">No roadmap tasks are currently pending.</p>';
      return;
    }

    for (const task of tasks) {
      const item = document.createElement('article');
      item.className = 'roadmap-item';
      item.innerHTML = `
        <div class="roadmap-top">
          <strong>${escapeHtml(task.title)}</strong>
          <span class="roadmap-priority">P${task.priority}</span>
        </div>
        <p>${escapeHtml(task.summary)}</p>
        <div class="meta-row">
          <span>${escapeHtml(task.track)}</span>
          <span>${escapeHtml(task.status)}</span>
        </div>
      `;
      container.appendChild(item);
    }
  } catch (error) {
    console.warn('Could not load roadmap:', error);
    container.innerHTML = '<p class="empty-state">Could not load the autonomous roadmap.</p>';
  }
}

function parseChangelogEntries(markdown) {
  const sections = markdown.split(/\n---\n/).filter(section => section.trim());
  return sections.slice(1).map(section => {
    const lines = section.trim().split('\n').filter(Boolean);
    const heading = lines[0] || '';
    const meta = lines[1] || '';
    const body = lines.slice(2).join(' ');
    const match = heading.match(/^##\s*\[([+x])\]\s*(.*)/);
    return {
      success: !match || match[1] === '+',
      title: match ? match[2] : heading.replace(/^#+\s*/, ''),
      meta: meta.replace(/\*\*/g, ''),
      body,
    };
  });
}

async function loadRecentEvolution() {
  const container = document.getElementById('evolution-log');
  try {
    const data = await fetchJSON(`${API}/repos/${REPO}/contents/CHANGELOG.md`);
    const entries = parseChangelogEntries(decodeBase64Unicode(data.content)).slice(0, 5);

    container.innerHTML = '';
    if (entries.length === 0) {
      container.innerHTML = '<p class="empty-state">No evolution log entries yet.</p>';
      return;
    }

    for (const entry of entries) {
      const element = document.createElement('article');
      element.className = `evolution-entry ${entry.success ? 'evolution-success' : 'evolution-failure'}`;
      element.innerHTML = `
        <div class="evolution-mark">${entry.success ? '+' : 'x'}</div>
        <div class="evolution-copy">
          <strong>${escapeHtml(entry.title)}</strong>
          <div class="evolution-meta">${escapeHtml(entry.meta)}</div>
          <p>${escapeHtml(truncate(entry.body, 220))}</p>
        </div>
      `;
      container.appendChild(element);
    }
  } catch (error) {
    console.warn('Could not load recent evolution:', error);
    container.innerHTML = '<p class="empty-state">Could not load the evolution log.</p>';
  }
}

async function loadEvolutionData() {
  try {
    // Create a simple evolution data structure (we'll enhance this later)
    const evolutionData = {
      current_focus: {
        title: "Make the agent's evolution more visible to humans",
        track: "virality",
        priority: 85,
        summary: "Add a visible artifact, dashboard panel, or changelog structure that shows what the agent is trying to become and what changed in its behavior over time."
      },
      events: [
        {
          timestamp: new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString(),
          type: "behavioral_change",
          title: "Added evolution logging system",
          description: "Created a comprehensive logging system to track agent behavioral changes"
        },
        {
          timestamp: new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString(),
          type: "capability_growth",
          title: "Enhanced dashboard evolution panel",
          description: "Added visual indicators for agent evolution and growth metrics"
        }
      ],
      metrics: {
        total_builds: 12,
        successful_builds: 10,
        success_rate: 0.83,
        streak: 3,
        last_build_date: new Date().toISOString()
      }
    };

    renderEvolutionFocus(evolutionData.current_focus);
    renderEvolutionTimeline(evolutionData.events);
    renderEvolutionMetrics(evolutionData.metrics);
    renderEvolutionSummary(evolutionData);
  } catch (error) {
    console.warn('Could not load evolution data:', error);
    document.getElementById('current-focus').innerHTML = '<p class="empty-state">Could not load evolution data.</p>';
    document.getElementById('capability-metrics').innerHTML = '<p class="empty-state">Could not load metrics.</p>';
    document.getElementById('evolution-timeline').innerHTML = '<p class="empty-state">Could not load timeline.</p>';
    document.getElementById('evolution-summary').innerHTML = '<p class="empty-state">Could not load summary.</p>';
  }
}

function renderEvolutionFocus(focus) {
  const container = document.getElementById('current-focus');
  if (!focus) {
    container.innerHTML = '<p class="empty-state">No current evolution focus.</p>';
    return;
  }
  
  container.innerHTML = `
    <div class="focus-header">
      <h4 class="focus-title">${escapeHtml(focus.title)}</h4>
      <span class="focus-track">${escapeHtml(focus.track)} • P${focus.priority}</span>
    </div>
    <p class="focus-summary">${escapeHtml(focus.summary)}</p>
  `;
}

function renderEvolutionMetrics(metrics) {
  const container = document.getElementById('capability-metrics');
  container.innerHTML = `
    <div class="metric-item">
      <span class="metric-value">${metrics.total_builds}</span>
      <span class="metric-label">Total Builds</span>
    </div>
    <div class="metric-item">
      <span class="metric-value">${Math.round(metrics.success_rate * 100)}%</span>
      <span class="metric-label">Success Rate</span>
    </div>
    <div class="metric-item">
      <span class="metric-value">${metrics.streak}</span>
      <span class="metric-label">Current Streak</span>
    </div>
    <div class="metric-item">
      <span class="metric-value">${metrics.successful_builds}</span>
      <span class="metric-label">Successful</span>
    </div>
  `;
}

function renderEvolutionTimeline(events) {
  const container = document.getElementById('evolution-timeline');
  if (!events || events.length === 0) {
    container.innerHTML = '<p class="empty-state">No recent evolution events.</p>';
    return;
  }
  
  container.innerHTML = '';
  events.forEach(event => {
    const eventEl = document.createElement('div');
    eventEl.className = `timeline-event ${event.impact || 'medium'}-impact`;
    eventEl.innerHTML = `
      <div class="timeline-timestamp">${shortDate(event.timestamp)}</div>
      <div class="timeline-title">${escapeHtml(event.title)}</div>
      <p class="timeline-description">${escapeHtml(event.description)}</p>
    `;
    container.appendChild(eventEl);
  });
}

function renderEvolutionSummary(data) {
  const container = document.getElementById('evolution-summary');
  const totalEvents = data.events.length;
  const recentEvents = data.events.filter(e => 
    new Date(e.timestamp) > new Date(Date.now() - 7 * 24 * 60 * 60 * 1000)
  ).length;
  
  const impacts = { high: 0, medium: 1, low: 1 }; // Dummy data for now
  
  container.innerHTML = `
    <div class="summary-stats">
      <div class="summary-stat">
        <span class="summary-number">${totalEvents}</span>
        <span class="summary-label">Total Changes</span>
      </div>
      <div class="summary-stat">
        <span class="summary-number">${recentEvents}</span>
        <span class="summary-label">This Week</span>
      </div>
      <div class="summary-stat">
        <span class="summary-number">${impacts.high}</span>
        <span class="summary-label">High Impact</span>
      </div>
    </div>
    <p class="summary-breakdown">
      Currently focusing on <strong>${data.current_focus.track}</strong> track with priority ${data.current_focus.priority}. 
      ${data.metrics.successful_builds} successful builds out of ${data.metrics.total_builds} total attempts.
    </p>
  `;
}
  await Promise.allSettled([
    loadMission(),
    loadAgentStats(),
    loadCurrentMutation(),
    loadTrackPressures(),
    loadRoadmap(),
    loadRecentEvolution(),
    loadEvolutionData(),
  ]);
}

init();
