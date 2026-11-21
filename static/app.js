(() => {
  const form = document.getElementById('download-form');
  const urlInput = document.getElementById('url');
  const previewButton = document.getElementById('preview-button');
  const downloadButton = document.getElementById('download-button');
  const videoQuality = document.getElementById('video-quality');
  const videoLabel = document.getElementById('video-label');
  const audioQuality = document.getElementById('audio-quality');
  const audioLabel = document.getElementById('audio-label');
  const preview = document.getElementById('preview');
  const message = document.getElementById('message');
  const loading = document.getElementById('loading');
  const image = document.getElementById('preview-image');
  const cookieInput = document.getElementById('cookies');
  const cookieAck = document.getElementById('cookie-ack');
  const cookieDropzone = document.getElementById('cookie-dropzone');
  const cookieDropTarget = document.getElementById('cookie-drop-target');
  const chooseCookie = document.getElementById('choose-cookie');
  const removeCookie = document.getElementById('remove-cookie');
  const cookieFileState = document.getElementById('cookie-file-state');
  const cookieFileName = document.getElementById('cookie-file-name');
  const cookieFileSize = document.getElementById('cookie-file-size');
  const cookieMessage = document.getElementById('cookie-message');
  const cookieManager = document.getElementById('cookie-manager');
  const browserCookieBrowser = document.getElementById('browser-cookie-browser');
  const browserCookieProfile = document.getElementById('browser-cookie-profile');
  const browserCookieKeyring = document.getElementById('browser-cookie-keyring');
  const browserCookieAck = document.getElementById('browser-cookie-ack');
  const maxCookieBytes = Number(cookieManager?.dataset.maxBytes || 0);
  let previewKey = '';

  function selectedFormat() {
    return form.querySelector('input[name="format"]:checked').value;
  }

  function selectedQuality() {
    if (selectedFormat() === 'mp3') return audioQuality.value;
    return selectedFormat() === 'original' ? 'best' : videoQuality.value;
  }

  function currentKey() {
    const file = cookieInput?.files?.[0];
    const cookieState = file ? `${file.name}:${file.size}:${file.lastModified}` : '';
    return `${urlInput.value.trim()}\n${selectedFormat()}\n${selectedQuality()}\n${cookieState}\n${cookieAck?.checked ? 'acknowledged' : 'not-acknowledged'}\n${browserCookieBrowser?.value || ''}\n${browserCookieProfile?.value || ''}\n${browserCookieKeyring?.value || ''}\n${browserCookieAck?.checked ? 'browser-acknowledged' : 'not-acknowledged'}`;
  }

  function hasCookieConsent() {
    if (cookieInput?.files?.length && !cookieAck?.checked) {
      showError('Confirm that you trust this server before sending account cookies.');
      return false;
    }
    return true;
  }

  function hasBrowserCookieConsent() {
    if (browserCookieBrowser?.value && !browserCookieAck?.checked) {
      showError('Confirm that you authorize local browser-profile access for this request.');
      return false;
    }
    return true;
  }

  function readableBytes(size) {
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
    return `${(size / (1024 * 1024)).toFixed(2)} MiB`;
  }

  function renderCookieFile() {
    const file = cookieInput?.files?.[0];
    if (!file) {
      if (cookieFileState) cookieFileState.hidden = true;
      if (cookieDropzone) cookieDropzone.classList.remove('has-file');
      return;
    }
    cookieFileName.textContent = file.name;
    cookieFileSize.textContent = `${readableBytes(file.size)} · selected in this tab`;
    cookieFileState.hidden = false;
    cookieDropzone.classList.add('has-file');
  }

  function reportCookieIssue(text) {
    if (!cookieMessage) return;
    cookieMessage.textContent = text;
    cookieMessage.hidden = false;
  }

  function setDroppedFile(file) {
    if (!cookieInput || !file) return;
    if (maxCookieBytes && file.size > maxCookieBytes) {
      reportCookieIssue(`This file is too large. Choose a file under ${readableBytes(maxCookieBytes)}.`);
      return;
    }
    try {
      const transfer = new DataTransfer();
      transfer.items.add(file);
      cookieInput.files = transfer.files;
      cookieInput.dispatchEvent(new Event('change', { bubbles: true }));
    } catch (_) {
      reportCookieIssue('This browser cannot attach dropped files. Use Browse files instead.');
    }
  }

  function refreshOptions() {
    const format = selectedFormat();
    const audio = format === 'mp3';
    const automatic = format === 'original';
    videoLabel.classList.toggle('hidden', audio || automatic);
    videoQuality.classList.toggle('hidden', audio || automatic);
    videoQuality.disabled = audio || automatic;
    videoQuality.name = !audio && !automatic ? 'quality' : '';
    audioQuality.classList.toggle('hidden', !audio);
    audioQuality.disabled = !audio;
    audioQuality.name = audio ? 'quality' : '';
    audioLabel.classList.toggle('hidden', !audio);
    clearPreview();
  }

  function clearPreview() {
    previewKey = '';
    downloadButton.disabled = true;
    preview.hidden = true;
    image.hidden = true;
    message.hidden = true;
    message.textContent = '';
  }

  function formatDuration(seconds) {
    if (!Number.isFinite(seconds)) return '';
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return mins >= 60
      ? `${Math.floor(mins / 60)} hr ${mins % 60} min`
      : `${mins} min ${String(secs).padStart(2, '0')} sec`;
  }

  function showError(text) {
    message.textContent = text || 'Something went wrong. Please try again.';
    message.className = 'message message-error';
    message.hidden = false;
  }

  form.querySelectorAll('input[name="format"]').forEach((radio) => radio.addEventListener('change', refreshOptions));
  [urlInput, videoQuality, audioQuality, browserCookieProfile].filter(Boolean).forEach((control) => control.addEventListener('input', clearPreview));
  form.querySelectorAll('select').forEach((control) => control.addEventListener('change', clearPreview));
  cookieInput?.addEventListener('change', () => {
    const file = cookieInput.files?.[0];
    if (file && maxCookieBytes && file.size > maxCookieBytes) {
      cookieInput.value = '';
      cookieAck.checked = false;
      renderCookieFile();
      reportCookieIssue(`This file is too large. Choose a file under ${readableBytes(maxCookieBytes)}.`);
    } else {
      cookieAck.checked = false;
      if (cookieMessage) cookieMessage.hidden = true;
      renderCookieFile();
    }
    clearPreview();
  });
  cookieAck?.addEventListener('change', clearPreview);
  browserCookieAck?.addEventListener('change', clearPreview);

  chooseCookie?.addEventListener('click', () => cookieInput?.click());
  cookieDropTarget?.addEventListener('click', () => cookieInput?.click());
  cookieDropTarget?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      cookieInput?.click();
    }
  });
  removeCookie?.addEventListener('click', () => {
    cookieInput.value = '';
    cookieAck.checked = false;
    if (cookieMessage) cookieMessage.hidden = true;
    renderCookieFile();
    clearPreview();
  });
  cookieDropzone?.addEventListener('dragover', (event) => {
    event.preventDefault();
    cookieDropzone.classList.add('is-dragging');
  });
  cookieDropzone?.addEventListener('dragleave', () => cookieDropzone.classList.remove('is-dragging'));
  cookieDropzone?.addEventListener('drop', (event) => {
    event.preventDefault();
    cookieDropzone.classList.remove('is-dragging');
    const files = event.dataTransfer?.files;
    if (files?.length > 1) {
      reportCookieIssue('Choose one cookie file at a time.');
      return;
    }
    if (files?.length) setDroppedFile(files[0]);
  });

  previewButton.addEventListener('click', async () => {
    clearPreview();
    const url = urlInput.value.trim();
    if (!urlInput.reportValidity()) return;
    if (!hasCookieConsent() || !hasBrowserCookieConsent()) return;
    previewButton.disabled = true;
    previewButton.textContent = 'Checking…';
    message.textContent = 'Checking the link and loading media details…';
    message.className = 'message';
    message.hidden = false;

    const data = new FormData(form);
    data.set('quality', selectedQuality());
    try {
      const response = await fetch('/api/info', {
        method: 'POST',
        body: data,
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) {
        const fallback = response.status === 413
          ? 'The request or cookie file exceeds the server upload limit.'
          : 'Could not read this media link.';
        throw new Error(result.error || fallback);
      }

      document.getElementById('preview-title').textContent = result.title || 'Untitled media';
      const details = [result.extractor, result.uploader, formatDuration(result.duration)].filter(Boolean);
      document.getElementById('preview-details').textContent = details.join(' · ');
      if (result.thumbnail) {
        image.src = result.thumbnail;
        image.hidden = false;
      }
      preview.hidden = false;
      message.hidden = true;
      previewKey = currentKey();
      downloadButton.disabled = false;
    } catch (error) {
      showError(error.message);
    } finally {
      previewButton.disabled = false;
      previewButton.textContent = 'Preview link';
    }
  });

  form.addEventListener('submit', (event) => {
    if (!hasCookieConsent() || !hasBrowserCookieConsent()) {
      event.preventDefault();
      return;
    }
    if (!previewKey || currentKey() !== previewKey) {
      event.preventDefault();
      showError('Preview this link again after changing the URL or quality.');
      downloadButton.disabled = true;
      return;
    }
    downloadButton.disabled = true;
    downloadButton.textContent = 'Preparing…';
    loading.hidden = false;
  });

  fetch('/api/platforms')
    .then((response) => response.ok ? response.json() : Promise.reject(new Error('Unavailable')))
    .then((result) => {
      const list = document.getElementById('platform-list');
      list.replaceChildren();
      result.examples.forEach((name) => {
        const chip = document.createElement('span');
        chip.className = 'platform-chip';
        chip.textContent = name;
        list.appendChild(chip);
      });
      if (!result.examples.length) list.textContent = 'Use any site supported by yt-dlp.';
      document.getElementById('platform-note').textContent = result.note;
    })
    .catch(() => {
      document.getElementById('platform-list').textContent = 'Use any site supported by yt-dlp.';
    });

  refreshOptions();
})();
