// Run ONLY against a dedicated synthetic rehearsal election. See loadtest/README.md.
import http from 'k6/http';
import { check, fail, sleep } from 'k6';
import { SharedArray } from 'k6/data';
import { Rate, Trend } from 'k6/metrics';

const base = (__ENV.BASE_URL || 'http://localhost:8000').replace(/\/$/, '');
const mode = __ENV.MODE || 'steady';
const users = new SharedArray('synthetic voters', () => JSON.parse(open(__ENV.VOTERS_FILE || './voters.json')));
const failures = new Rate('unexpected_errors');
const voteLatency = new Trend('voting_duration', true);
const loginLatency = new Trend('login_duration', true);
let authenticated = false;
let cast = false;

export const options = {
  noCookiesReset: true,
  scenarios: mode === 'smoke' ? {
    smoke: { executor: 'per-vu-iterations', vus: 2, iterations: 2, maxDuration: '2m' },
  } : mode === 'burst' ? {
    burst: { executor: 'per-vu-iterations', vus: 500, iterations: 1, maxDuration: '2m' },
  } : {
    voting: { executor: 'ramping-vus', startVUs: 0, stages: [
      { duration: '60s', target: 500 }, { duration: '15m', target: 500 }, { duration: '10s', target: 0 },
    ], gracefulRampDown: '30s' },
  },
  thresholds: {
    unexpected_errors: ['rate<0.005'],
    checks: ['rate==1'],
    login_duration: ['p(95)<10000'],
    ...(mode === 'burst' ? {} : { voting_duration: ['p(95)<2000'] }),
  },
};

export function setup() {
  if (__ENV.ALLOW_SYNTHETIC_ELECTION !== 'yes') fail('Set ALLOW_SYNTHETIC_ELECTION=yes for an isolated rehearsal.');
  if (users.length < (mode === 'smoke' ? 2 : 500)) fail('Provide enough distinct synthetic voters (500 for the full gate).');
  if (new Set(users.map(u => u.nim)).size !== users.length) fail('Synthetic voters must be unique.');
  const page = http.get(`${base}/`);
  if (page.html().find('[data-rehearsal="true"]').size() !== 1) fail('This harness only runs against an explicitly named DEMO election.');
}

function token(response) {
  const value = response.html().find('input[name="csrfmiddlewaretoken"]').first().attr('value');
  if (!value) fail('Missing CSRF token');
  return value;
}

function record(response) {
  failures.add(response.status >= 500 || response.status === 0 || response.status === 429);
  return response;
}

function login(user) {
  const started = Date.now();
  const page = record(http.get(`${base}/login/`));
  const response = record(http.post(`${base}/login/`, {
    csrfmiddlewaretoken: token(page), username: user.nim, password: user.password, next: '/dashboard/',
  }, { headers: { Referer: `${base}/login/` } }));
  // Includes redirect and dashboard rendering, as seen by the voter.
  loginLatency.add(Date.now() - started);
  if (!check(response, { 'login reaches dashboard': r => r.status === 200 && r.url.includes('/dashboard/') })) {
    fail('Login failed');
  }
  authenticated = true;
}

function votingRequest(method, url, data = null, params = {}) {
  const started = Date.now();
  const response = record(http.request(method, url, data, params));
  voteLatency.add(Date.now() - started);
  return response;
}

function officeVote(user, office) {
  const page = votingRequest('GET', `${base}/vote/${office}/`);
  if (page.url.includes('/receipt/')) fail('Rehearsal voters have existing votes; use a fresh database.');
  const candidates = page.html().find('input[name="candidate"]');
  const candidate = candidates.eq(__VU % 2).attr('value');
  const requestId = page.html().find('input[name="request_id"]').attr('value');
  if (!candidate || !requestId) fail('Ballot is unavailable; check election opening and pause status.');
  const payload = { csrfmiddlewaretoken: token(page), candidate, request_id: requestId, action: 'review' };
  const reviewed = votingRequest('POST', `${base}/vote/${office}/`, payload, { headers: { Referer: page.url } });
  if (!check(reviewed, { 'confirmation shown': r => r.status === 200 && r.html().find('button[value="submit"]').size() === 1 })) {
    fail('Review failed');
  }
  payload.csrfmiddlewaretoken = token(reviewed);
  payload.action = 'submit';
  const result = votingRequest('POST', `${base}/vote/${office}/`, payload, { headers: { Referer: page.url } });
  const receipt = result.url.match(/\/receipt\/([0-9a-f-]+)\//);
  if (!check(result, { 'receipt acknowledged': r => r.status === 200 && Boolean(receipt) })) fail('Submission failed');
  console.log(JSON.stringify({ type: 'receipt', nim: user.nim, office, receipt_id: receipt[1] }));
  // Re-submit with another candidate: the existing receipt must be returned.
  payload.candidate = candidates.eq((__VU + 1) % 2).attr('value');
  const retry = votingRequest('POST', `${base}/vote/${office}/`, payload, { headers: { Referer: page.url } });
  check(retry, { 'retry preserves original receipt': r => r.status === 200 && r.url === result.url });
}

export default function () {
  const user = users[__VU - 1];
  if (!authenticated) login(user);
  if (mode === 'burst') return;
  if (!cast) {
    officeVote(user, 'kahim');
    sleep(1 + Math.random() * 2);
    officeVote(user, 'senator');
    cast = true;
  }
  const dashboard = votingRequest('GET', `${base}/dashboard/`);
  check(dashboard, { 'session remains authenticated': r => r.status === 200 && r.url.includes('/dashboard/') });
  sleep(5 + Math.random() * 10);
}

export function handleSummary(data) {
  return { [`output/load-${mode}-summary.json`]: JSON.stringify(data, null, 2) };
}
