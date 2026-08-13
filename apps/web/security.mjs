import crypto from 'node:crypto';

const isProductionMode = (env) => env.PLATFORM_MODE === 'live';

function equalSecret(received, expected) {
  const left = Buffer.from(received || '');
  const right = Buffer.from(expected || '');
  if (left.length !== right.length || right.length < 32) return false;
  return crypto.timingSafeEqual(left, right);
}

export function assertProductionSecurity(env = process.env) {
  if (!isProductionMode(env)) return;
  if (!env.PLATFORM_API_TOKEN || env.PLATFORM_API_TOKEN.length < 32) {
    throw new Error('platform_api_token_required');
  }
}

export function requestAuthorized(request, env = process.env) {
  if (!isProductionMode(env)) return true;
  const authorization = String(request.headers.authorization || '');
  const [scheme, token] = authorization.split(' ', 2);
  return scheme === 'Bearer' && equalSecret(token, env.PLATFORM_API_TOKEN);
}
