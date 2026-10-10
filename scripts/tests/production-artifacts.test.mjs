import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

const read = (name) =>
  fs.readFileSync(new URL(`../../${name}`, import.meta.url), 'utf8');

test('production Dockerfiles lock browser/Python/Node and install only at build time', () => {
  const backend = read('backend/Dockerfile');
  assert.match(backend, /playwright\/python:v1\.62\.0-noble/);
  assert.match(backend, /node:22\.22\.3-bookworm-slim/);
  assert.match(backend, /uv sync --locked --no-dev/);
  assert.match(backend, /USER 10001:10001/);
  assert.match(backend, /UV_PROJECT_ENVIRONMENT=\/opt\/venv/);
  assert.match(read('backend/pyproject.toml'), /"playwright==1\.62\.0"/);
  assert.equal(read('backend/.python-version').trim(), '3.12');
  const entrypoint = read('deployment/backend-entrypoint.sh');
  assert.match(entrypoint, /umask 077/);
  assert.match(entrypoint, /--workers 1 --no-proxy-headers --no-access-log/);
  assert.doesNotMatch(
    entrypoint,
    /uv sync|pip install|playwright install|inference|refresh/,
  );
});

test('frontend permissions work without BuildKit and runtime stays nonroot', () => {
  const dockerfile = read('frontend/Dockerfile');
  assert.doesNotMatch(dockerfile, /COPY --chmod/);
  assert.match(
    dockerfile,
    /USER root\s+RUN chmod 0555 \/usr\/local\/bin\/frontend-entrypoint\s+USER 101:101/,
  );
});

test('production Compose isolates API, storage, secrets and sandbox', () => {
  const compose = read('compose.yaml');
  const backend = compose.split(/\n  backend:/)[1].split('\nvolumes:')[0];
  assert.doesNotMatch(backend, /\n    ports:/);
  assert.match(backend, /init: true/);
  assert.match(backend, /shm_size: 1gb/);
  assert.match(backend, /read_only: true/);
  assert.match(backend, /cap_drop: \[ALL\]/);
  assert.match(backend, /seccomp=\.\/deployment\/chromium-seccomp.json/);
  assert.match(compose, /127\.0\.0\.1:8080:8080/);
  assert.match(
    backend,
    /FIREBASE_SERVICE_ACCOUNT_FILE: \/run\/secrets\/firebase_service_account/,
  );
  assert.match(
    backend,
    /WW_CREDENTIALS_KEY_FILE: \/run\/secrets\/ww_credentials_key/,
  );
  assert.match(backend, /WW_AUTH_TRUSTED_PROXY: 172\.30\.87\.2/);
  assert.match(backend, /WW_BROWSER_PROFILE: \/app\/browser-profile/);
  // First-admin bootstrap is pinned to an operator-chosen owner, never first come.
  assert.match(
    backend,
    /WW_BOOTSTRAP_ADMIN_EMAIL: \$\{WW_BOOTSTRAP_ADMIN_EMAIL:\?[^}]+\}/,
  );
  assert.match(
    backend,
    /WW_BOOTSTRAP_ADMIN_UID: \$\{WW_BOOTSTRAP_ADMIN_UID:-\}/,
  );
  const example = read('deployment/production.env.example');
  assert.match(example, /^WW_BOOTSTRAP_ADMIN_EMAIL=owner@example\.invalid$/m);
  assert.match(example, /^WW_BOOTSTRAP_ADMIN_UID=$/m);
  assert.doesNotMatch(
    compose,
    /privileged:|SYS_ADMIN|unconfined|ipc: host|network_mode: host|VITE_.*KEY/,
  );
  const profile = JSON.parse(read('deployment/chromium-seccomp.json'));
  assert.equal(profile.defaultAction, 'SCMP_ACT_ERRNO');
  assert.ok(
    profile.syscalls.some(
      (rule) =>
        rule.action === 'SCMP_ACT_ALLOW' &&
        ['clone', 'setns', 'unshare'].every((name) =>
          rule.names.includes(name),
        ),
    ),
  );
});

test('seccomp supports runc secure path resolution without disabling default denial', () => {
  const profile = JSON.parse(read('deployment/chromium-seccomp.json'));
  assert.equal(profile.defaultAction, 'SCMP_ACT_ERRNO');
  assert.ok(
    profile.syscalls.some(
      (rule) =>
        rule.names.includes('openat2') && rule.action === 'SCMP_ACT_ALLOW',
    ),
  );
});

test('Chromium can chroot inside its own user namespace without container capabilities', () => {
  const profile = JSON.parse(read('deployment/chromium-seccomp.json'));
  const rules = profile.syscalls.filter((rule) =>
    rule.names.includes('chroot'),
  );
  assert.equal(rules.length, 1);
  assert.equal(rules[0].action, 'SCMP_ACT_ALLOW');
  assert.deepEqual(rules[0].includes, {});
  assert.deepEqual(rules[0].excludes, {});
  assert.match(read('compose.yaml'), /cap_drop: \[ALL\]/);
  assert.doesNotMatch(read('compose.yaml'), /cap_add:/);
});

test('seccomp returns ENOSYS for clone3 without permitting the syscall', () => {
  const profile = JSON.parse(read('deployment/chromium-seccomp.json'));
  const rules = profile.syscalls.filter((rule) =>
    rule.names.includes('clone3'),
  );
  assert.equal(profile.defaultAction, 'SCMP_ACT_ERRNO');
  assert.equal(rules.length, 1);
  assert.equal(rules[0].action, 'SCMP_ACT_ERRNO');
  assert.equal(rules[0].errnoRet, 38);
  assert.deepEqual(rules[0].names, ['clone3']);
  assert.deepEqual(rules[0].args, []);
});

test('nginx exact-host TLS boundary strips forwarding, disables SSE buffering and preserves SPA', () => {
  const nginx = read('deployment/nginx.conf.template');
  assert.match(nginx, /\$http_host != "\$\{WW_PUBLIC_HOST\}"/);
  assert.match(nginx, /proxy_set_header Host "\$\{WW_PUBLIC_HOST\}"/);
  assert.match(nginx, /proxy_set_header X-Forwarded-Proto https/);
  for (const header of [
    'Forwarded',
    'X-Forwarded-For',
    'X-Forwarded-Host',
    'X-Forwarded-Port',
    'X-Real-IP',
  ])
    assert.match(nginx, new RegExp(`proxy_set_header ${header} ""`));
  assert.match(nginx, /proxy_buffering off/);
  assert.match(nginx, /proxy_cache off/);
  assert.match(nginx, /location ~ \^\/\(api\|doc\)/);
  assert.match(nginx, /try_files \$uri \$uri\/ \/index.html/);
  assert.match(nginx, /access_log off/);
});

test('build context excludes private input and secret-bearing files', () => {
  const exclusions = read('.dockerignore').split(/\r?\n/);
  for (const item of [
    '.git',
    '.credentials.json',
    '.experiment',
    'data',
    'privatekeys',
    '**/.env',
    '**/.env.*',
    '**/*.pem',
    '**/*.key',
    '**/*.sqlite3',
    '**/*service-account*.json',
  ])
    assert.ok(exclusions.includes(item), item);
  assert.doesNotMatch(read('backend/Dockerfile'), /COPY \. /);
  assert.doesNotMatch(read('frontend/Dockerfile'), /ARG .*KEY|ARG .*SECRET/);
});
