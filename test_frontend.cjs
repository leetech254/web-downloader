const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'static/app.js'), 'utf8');

class FakeElement {
  constructor(id) {
    this.id = id;
    this.value = '';
    this.textContent = '';
    this.className = '';
    this.disabled = false;
    this.hidden = false;
    this.checked = false;
    this.files = [];
    this.dataset = {};
    this.attributes = {};
    this.handlers = {};
    this.children = [];
    this.clickCount = 0;
    this.blockFirstClick = false;
    this.classList = {
      add: () => {},
      remove: () => {},
      toggle: () => {},
    };
  }

  addEventListener(name, callback) {
    this.handlers[name] = callback;
  }

  setAttribute(name, value) {
    this.attributes[name] = value;
  }

  removeAttribute(name) {
    delete this.attributes[name];
    if (name === 'href') this.href = '';
  }

  click() {
    if (this.disabled) return undefined;
    this.clickCount += 1;
    if (this.blockFirstClick && this.clickCount === 1) {
      const error = new Error('Automatic download was blocked');
      error.name = 'NotAllowedError';
      throw error;
    }
    this.clicked = true;
    const result = this.handlers.click?.({ preventDefault() {} });
    if (result && typeof result.then === 'function') this.pendingClick = result;
    return result;
  }

  reportValidity() {
    return true;
  }

  replaceChildren() {
    this.children = [];
  }

  appendChild(child) {
    this.children.push(child);
    return child;
  }

  querySelector(selector) {
    if (this.id === 'loading' && selector === '.spinner') return this.spinner;
    return null;
  }

  querySelectorAll() {
    return [];
  }
}

function makeEnvironment(downloadResponse, { mediaType = 'video', hasAudio = true, infoResponse = null } = {}) {
  const elements = new Map();
  const scheduledTimers = [];
  const get = (id) => {
    if (!elements.has(id)) elements.set(id, new FakeElement(id));
    return elements.get(id);
  };

  const form = get('download-form');
  const url = get('url');
  url.value = 'https://media.example.test/watch/123';
  const formatRadios = [get('format-auto'), get('format-mp4'), get('format-mp3')];
  formatRadios[0].value = 'auto';
  formatRadios[0].checked = true;
  formatRadios[1].value = 'mp4';
  formatRadios[2].value = 'mp3';
  const videoQuality = get('video-quality');
  videoQuality.value = '720';
  const audioQuality = get('audio-quality');
  audioQuality.value = '192';
  audioQuality.disabled = true;
  const spinner = get('spinner');
  get('loading').spinner = spinner;

  form.querySelector = (selector) => selector === 'input[name="format"]:checked' ? formatRadios.find((radio) => radio.checked) : null;
  form.querySelectorAll = (selector) => {
    if (selector === 'input[name="format"]') return formatRadios;
    if (selector === 'select') return [videoQuality, audioQuality];
    if (selector === 'input:not([type="hidden"]), select') return [url, ...formatRadios, videoQuality, audioQuality];
    return [];
  };

  const document = {
    getElementById: get,
    createElement: (tag) => new FakeElement(tag),
    body: get('body'),
  };

  let objectUrlNumber = 0;
  const objectUrls = new Set();
  const mockURL = {
    createObjectURL: () => {
      const urlValue = `blob:mock-download-${++objectUrlNumber}`;
      objectUrls.add(urlValue);
      return urlValue;
    },
    revokeObjectURL: (urlValue) => objectUrls.delete(urlValue),
  };

  class FakeFormData {
    set() {}
  }

  const fetchCalls = [];
  const fetch = async (urlValue) => {
    fetchCalls.push(urlValue);
    if (urlValue === '/api/info') {
      if (infoResponse) return infoResponse;
      return {
        ok: true,
        json: async () => ({ title: 'Sample media', extractor: 'Example', duration: 10, media_type: mediaType, has_audio: hasAudio }),
      };
    }
    if (urlValue === '/download') return downloadResponse;
    throw new Error(`Unexpected fetch URL: ${urlValue}`);
  };

  const context = {
    document,
    fetch,
    URL: mockURL,
    FormData: FakeFormData,
    Response,
    Blob,
    ReadableStream,
    TypeError,
    Error,
    setTimeout: (callback) => {
      scheduledTimers.push(callback);
      return scheduledTimers.length;
    },
    console,
  };
  vm.runInNewContext(source, context, { filename: 'static/app.js' });

  return {
    elements,
    form,
    formatRadios,
    fetchCalls,
    objectUrls,
    preview: async () => get('preview-button').handlers.click(),
    paste: async (value) => {
      url.value = value;
      url.handlers.paste();
      url.handlers.input();
      scheduledTimers.splice(0).forEach((callback) => callback());
      await get('preview-button').pendingClick;
    },
    submit: async () => form.handlers.submit({ preventDefault() {} }),
  };
}

test('does not fetch platform examples because they are server-rendered', () => {
  const env = makeEnvironment(new Response(new Uint8Array([1])));
  assert.equal(env.fetchCalls.includes('/api/platforms'), false);
});

test('defaults to Auto, identifies video, and keeps a valid preview after format and quality changes', async () => {
  const response = new Response(new Uint8Array([1]), {
    headers: { 'Content-Length': '1', 'Content-Type': 'video/mp4', 'Content-Disposition': 'attachment; filename="sample.mp4"' },
  });
  const env = makeEnvironment(response, { mediaType: 'video', hasAudio: true });
  assert.equal(env.formatRadios[0].checked, true);
  assert.match(env.elements.get('format-guidance').textContent, /Preview the link/i);
  await env.preview();

  assert.equal(env.elements.get('download-button').disabled, false);
  assert.match(env.elements.get('format-guidance').textContent, /Detected video/i);
  assert.match(env.elements.get('preview-details').textContent, /Video/);
  env.formatRadios[1].checked = true;
  env.formatRadios[1].handlers.change();
  env.elements.get('video-quality').value = '480';
  env.elements.get('video-quality').handlers.change();
  assert.equal(env.elements.get('preview').hidden, false);
  assert.equal(env.elements.get('download-button').disabled, false);
});

test('clears the URL and resets preview state with the clear control', async () => {
  const env = makeEnvironment(new Response(new Uint8Array([1])));
  await env.preview();
  const url = env.elements.get('url');
  const clear = env.elements.get('clear-url-button');
  assert.equal(clear.hidden, false);
  clear.click();
  assert.equal(url.value, '');
  assert.equal(clear.hidden, true);
  assert.equal(env.elements.get('preview').hidden, true);
  assert.equal(env.elements.get('download-button').disabled, true);
});

test('automatically previews a link after it is pasted', async () => {
  const env = makeEnvironment(new Response(new Uint8Array([1])));
  await env.paste('https://media.example.test/watch/pasted');

  assert.equal(env.elements.get('preview').hidden, false);
  assert.equal(env.elements.get('preview-title').textContent, 'Sample media');
  assert.equal(env.elements.get('download-button').disabled, false);
});

test('discards preview metadata when the request changes while lookup is pending', async () => {
  let resolveInfo;
  const infoResponse = new Promise((resolve) => { resolveInfo = resolve; });
  const env = makeEnvironment(new Response(new Uint8Array([1])), { infoResponse });
  const pendingPreview = env.preview();
  env.elements.get('url').value = 'https://media.example.test/watch/changed';
  env.elements.get('url').handlers.input();
  resolveInfo({
    ok: true,
    json: async () => ({ title: 'Old item', extractor: 'Example', duration: 10, media_type: 'video', has_audio: true }),
  });
  await pendingPreview;

  assert.equal(env.elements.get('preview').hidden, true);
  assert.equal(env.elements.get('download-button').disabled, true);
  assert.match(env.elements.get('message').textContent, /changed while the preview was loading/i);
});

test('disables incompatible formats and switches an image link to Auto when MP4 was chosen before preview', async () => {
  const response = new Response(new Uint8Array([1]), {
    headers: { 'Content-Length': '1', 'Content-Type': 'image/jpeg', 'Content-Disposition': 'attachment; filename="photo.jpg"' },
  });
  const env = makeEnvironment(response, { mediaType: 'image', hasAudio: false });
  env.formatRadios[0].checked = false;
  env.formatRadios[1].checked = true;
  await env.preview();

  assert.equal(env.formatRadios[0].checked, true);
  assert.equal(env.formatRadios[1].disabled, true);
  assert.equal(env.formatRadios[2].disabled, true);
  assert.match(env.elements.get('format-guidance').textContent, /Auto has been selected/i);
  assert.equal(env.elements.get('download-button').disabled, false);
});

test('streams byte progress, creates a save link, and reports received-not-disk-saved completion', async () => {
  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(new Uint8Array([1, 2]));
      controller.enqueue(new Uint8Array([3, 4]));
      controller.close();
    },
  });
  const response = new Response(body, {
    headers: {
      'Content-Length': '4',
      'Content-Type': 'video/mp4',
      'Content-Disposition': 'attachment; filename="sample.mp4"',
    },
  });
  const env = makeEnvironment(response);
  await env.preview();
  await env.submit();

  const progress = env.elements.get('download-progress');
  const label = env.elements.get('download-progress-label');
  const save = env.elements.get('save-download');
  const message = env.elements.get('message');
  assert.equal(progress.value, 100);
  assert.match(label.textContent, /4 B received/);
  assert.equal(save.hidden, false);
  assert.equal(save.download, 'sample.mp4');
  assert.equal(save.clickCount, 1);
  assert.match(message.textContent, /cannot confirm a disk save/);
  assert.equal(message.attributes.role, 'status');
});

test('turns structured 429 JSON and Retry-After into inline retry guidance and restores controls', async () => {
  const response = new Response(JSON.stringify({
    error: 'The downloader is busy. Please wait a few seconds and try again.',
    code: 'busy',
    retry_after: 15,
  }), {
    status: 429,
    headers: { 'Content-Type': 'application/json', 'Retry-After': '15' },
  });
  const env = makeEnvironment(response);
  await env.preview();
  await env.submit();

  const message = env.elements.get('message');
  assert.equal(message.className, 'message message-error');
  assert.equal(message.attributes.role, 'alert');
  assert.match(message.textContent, /retry in about 15 seconds/i);
  assert.equal(env.elements.get('loading').hidden, true);
  assert.equal(env.elements.get('save-download').hidden, true);
  assert.equal(env.elements.get('download-button').disabled, false);
});

test('retains a working manual save link when automatic browser saving is blocked', async () => {
  const response = new Response(new Uint8Array([7, 8, 9]), {
    headers: {
      'Content-Length': '3',
      'Content-Type': 'video/mp4',
      'Content-Disposition': 'attachment; filename="fallback.mp4"',
    },
  });
  const env = makeEnvironment(response);
  env.elements.get('save-download').blockFirstClick = true;
  await env.preview();
  await env.submit();

  const save = env.elements.get('save-download');
  const message = env.elements.get('message');
  assert.equal(save.hidden, false);
  assert.equal(save.download, 'fallback.mp4');
  assert.equal(save.clickCount, 1);
  assert.match(message.textContent, /Use “Save fallback\.mp4 now” below/);
  save.click();
  assert.equal(save.clicked, true);
  assert.equal(save.clickCount, 2);
});

test('shows a retryable interruption instead of a success state after a mid-transfer read failure', async () => {
  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(new Uint8Array([1, 2]));
      controller.error(new TypeError('connection terminated'));
    },
  });
  const response = new Response(body, {
    headers: { 'Content-Length': '10', 'Content-Type': 'video/mp4' },
  });
  const env = makeEnvironment(response);
  await env.preview();
  await env.submit();

  const message = env.elements.get('message');
  assert.equal(message.className, 'message message-error');
  assert.match(message.textContent, /connection was interrupted/i);
  assert.equal(env.elements.get('loading').hidden, true);
  assert.equal(env.elements.get('save-download').hidden, true);
  assert.equal(env.elements.get('download-button').disabled, false);
});
