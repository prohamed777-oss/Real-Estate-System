// k6 load test — golden flow: bootstrap → login → browse → create lead → search
// Usage: k6 run --env API=https://real-estate-system-production-a500.up.railway.app/api/v1 scripts/load_test.js
import http from 'k6/http';
import { check, sleep } from 'k6';

const API = __ENV.API || 'http://localhost:8000/api/v1';
const TOKEN = __ENV.TOKEN || '';

export const options = {
  stages: [
    { duration: '30s', target: 10 },   // ramp up
    { duration: '1m', target: 50 },    // sustained load
    { duration: '30s', target: 0 },    // ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<500', 'p(99)<1000'],
    http_req_failed: ['rate<0.01'],
  },
};

export default function () {
  const params = {
    headers: TOKEN ? { 'Authorization': `Bearer ${TOKEN}` } : { 'X-Dev-Email': 'owner@acme.test' },
  };

  // health
  let r = http.get(`${API.replace('/api/v1', '')}/health`);
  check(r, { 'health 200': (r) => r.status === 200 });

  // leads list
  r = http.get(`${API}/leads?limit=20`, params);
  check(r, { 'leads 200': (r) => r.status === 200 });

  // conversations
  r = http.get(`${API}/conversations?limit=10`, params);
  check(r, { 'conv 200': (r) => r.status === 200 });

  // properties
  r = http.get(`${API}/assets?limit=10`, params);
  check(r, { 'assets 200': (r) => r.status === 200 });

  // analytics overview
  r = http.get(`${API}/analytics/overview`, params);
  check(r, { 'analytics 200': (r) => r.status === 200 });

  // search
  r = http.get(`${API}/search/properties?city=Cairo`, params);
  check(r, { 'search 200': (r) => r.status === 200 });

  sleep(1);
}
