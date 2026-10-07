// Supply actors.json as a declared dependency. Each account is used by one load VU.

import { sleep } from 'k6';
import { SharedArray } from 'k6/data';
import execution from 'k6/execution';
import http from 'k6/http';

const actors = new SharedArray('social actors', () => JSON.parse(open('./actors.json')).actors);
let token = null;
export default function (context) {
  // All VU IDs belong to the single load scenario, so exactly peak_users
  // distinct accounts suffice and each VU retains its account across stages.
  const index = execution.vu.idInTest - 1;
  const actor = actors[index];
  if (!actor || !actor.username || !actor.password) throw new Error('missing actor');
  const timeout = (context.parameters.timeout_seconds || 5) + 's';
  function request(name, method, path, body, status) {
    return context.operation(
      name,
      () =>
        http.request(
          method,
          context.baseUrl + '/api/v1' + path,
          body === null ? null : JSON.stringify(body),
          {
            timeout,
            headers: {
              'Content-Type': 'application/json',
              ...(token ? { Authorization: 'Bearer ' + token } : {}),
            },
          },
        ),
      (response) => {
        if (response.status !== status) return false;
        if (status === 204) return true;
        try {
          const data = response.json();
          return name === 'login'
            ? typeof data.access_token === 'string'
            : name === 'feed' || name === 'comments'
              ? Array.isArray(data.items)
              : Number.isInteger(data.id);
        } catch (_) {
          return false;
        }
      },
    );
  }
  if (!token) {
    const login = request(
      'login',
      'POST',
      '/auth/login',
      { username: actor.username, password: actor.password },
      200,
    );
    if (login.status !== 200) {
      sleep(0.05);
      return;
    }
    token = login.json().access_token;
    if (!token) throw new Error('missing token');
    context.record('authenticated_account', {
      value: 1,
      unit: 'count',
      provider_fields: { account: actor.username },
    });
  }
  const feed = request('feed', 'GET', '/feed?limit=20', null, 200);
  if (feed.status !== 200) {
    if (feed.status === 401) token = null;
    sleep(0.05);
    return;
  }
  const posts = feed.json().items;
  if (posts.length) {
    const post = posts[0];
    request(
      post.liked ? 'unlike' : 'like',
      post.liked ? 'DELETE' : 'PUT',
      '/posts/' + post.id + '/like',
      null,
      204,
    );
    if (context.parameters.write_comments !== false)
      request(
        'comment',
        'POST',
        '/posts/' + post.id + '/comments',
        { content: 'benchmark reply' },
        201,
      );
  }
  sleep(context.parameters.wait_seconds === undefined ? 0.05 : context.parameters.wait_seconds);
}
