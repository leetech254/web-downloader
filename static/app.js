(() => {
  const form = document.getElementById('download-form');
  const urlInput = document.getElementById('url');
  const pasteLinkButton = document.getElementById('paste-link-button');
  const clearUrlButton = document.getElementById('clear-url-button');
  const previewButton = document.getElementById('preview-button');
  const downloadButton = document.getElementById('download-button');
  const videoQuality = document.getElementById('video-quality');
  const videoLabel = document.getElementById('video-label');
  const audioQuality = document.getElementById('audio-quality');
  const audioLabel = document.getElementById('audio-label');
  const formatGuidance = document.getElementById('format-guidance');
  const qualityGuidance = document.getElementById('quality-guidance');
  const formatRadios = Array.from(form.querySelectorAll('input[name="format"]'));
  const preview = document.getElementById('preview');
  const message = document.getElementById('message');
  const loading = document.getElementById('loading');
  const loadingText = document.getElementById('loading-text');
  const downloadProgress = document.getElementById('download-progress');
  const downloadProgressLabel = document.getElementById('download-progress-label');
  const saveDownload = document.getElementById('save-download');
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
  let downloadObjectUrl = '';
  let detectedMediaType = 'unknown';
  let detectedAudio = null;
  let hasPreviewedMedia = false;

  function selectedFormat() {
    return form.querySelector('input[name="format"]:checked').value;
  }

  function selectedQuality() {
    if (selectedFormat() === 'mp3') return audioQuality.value;
    return ['auto', 'original'].includes(selectedFormat()) ? 'best' : videoQuality.value;
  }

  function currentKey() {
    const file = cookieInput?.files?.[0];
    const cookieState = file ? `${file.name}:${file.size}:${file.lastModified}` : '';
    return `${urlInput.value.trim()}\n${cookieState}\n${cookieAck?.checked ? 'acknowledged' : 'not-acknowledged'}\n${browserCookieBrowser?.value || ''}\n${browserCookieProfile?.value || ''}\n${browserCookieKeyring?.value || ''}\n${browserCookieAck?.checked ? 'browser-acknowledged' : 'not-acknowledged'}`;
  }

  function updateUrlActions() {
    if (clearUrlButton) clearUrlButton.hidden = !urlInput.value.trim();
  }

  function setUrlValue(value) {
    urlInput.value = value.trim();
    updateUrlActions();
    clearPreview();
  }

  async function pasteFromClipboard() {
    if (typeof navigator === 'undefined' || !navigator.clipboard?.readText) {
      showError('Clipboard access is unavailable here. Paste the link into the field manually.');
      return;
    }
    pasteLinkButton.disabled = true;
    pasteLinkButton.textContent = 'Reading…';
    try {
      const value = (await navigator.clipboard.readText()).trim();
      if (!value) {
        showError('Your clipboard is empty. Copy a public media link first.');
        return;
      }
      setUrlValue(value);
      urlInput.focus?.();
      if (!/^https?:\/\//i.test(value)) {
        showError('The pasted text is not a complete HTTP or HTTPS link. Check it, then try again.');
        return;
      }
      previewButton.click();
    } catch (_) {
      showError('Clipboard permission was not granted. Paste the link into the field manually.');
    } finally {
      pasteLinkButton.disabled = false;
      pasteLinkButton.textContent = 'Paste link';
    }
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
    const automatic = format === 'auto' || format === 'original';
    videoLabel.classList.toggle('hidden', audio || automatic);
    videoQuality.classList.toggle('hidden', audio || automatic);
    videoQuality.disabled = audio || automatic;
    videoQuality.name = !audio && !automatic ? 'quality' : '';
    audioQuality.classList.toggle('hidden', !audio);
    audioQuality.disabled = !audio;
    audioQuality.name = audio ? 'quality' : '';
    audioLabel.classList.toggle('hidden', !audio);
    if (qualityGuidance) qualityGuidance.hidden = !automatic;
    updateFormatGuidance();
  }

  function clearPreview() {
    previewKey = '';
    downloadButton.disabled = true;
    detectedMediaType = 'unknown';
    detectedAudio = null;
    hasPreviewedMedia = false;
    formatRadios.forEach((radio) => { radio.disabled = false; });
    updateFormatGuidance();
    preview.hidden = true;
    image.hidden = true;
    message.hidden = true;
    message.textContent = '';
    message.className = 'message';
    message.setAttribute('role', 'status');
    loading.hidden = true;
    downloadProgress.hidden = true;
    downloadProgress.value = 0;
    downloadProgressLabel.hidden = true;
    downloadProgressLabel.textContent = '';
    saveDownload.hidden = true;
    saveDownload.removeAttribute('href');
    if (downloadObjectUrl) URL.revokeObjectURL(downloadObjectUrl);
    downloadObjectUrl = '';
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
    message.setAttribute('role', 'alert');
    message.hidden = false;
  }

  function showSuccess(text) {
    message.textContent = text;
    message.className = 'message message-success';
    message.setAttribute('role', 'status');
    message.hidden = false;
  }

  function setFormat(value) {
    const radio = formatRadios.find((item) => item.value === value);
    if (radio) radio.checked = true;
  }

  function updateFormatGuidance(reason = '') {
    if (!formatGuidance) return;
    if (reason) {
      formatGuidance.textContent = reason;
      return;
    }
    const format = selectedFormat();
    if (format === 'auto' && !hasPreviewedMedia) {
      formatGuidance.textContent = 'Auto is selected. Preview the link to identify its media type; Auto avoids forcing MP4 or MP3 conversion.';
      return;
    }
    if (format === 'mp4') {
      formatGuidance.textContent = 'MP4 video is selected. If the source cannot provide a compatible video stream, switch to Auto.';
    } else if (format === 'mp3') {
      formatGuidance.textContent = 'MP3 conversion is selected and needs an audio track. Choose Auto to keep a compatible source format.';
    } else if (detectedMediaType === 'video') {
      formatGuidance.textContent = 'Detected video. Auto lets yt-dlp choose a compatible source format; MP4 and MP3 remain optional.';
    } else if (detectedMediaType === 'audio') {
      formatGuidance.textContent = 'Detected audio-only media. Auto keeps a compatible audio format; MP4 is unavailable.';
    } else if (detectedMediaType === 'image') {
      formatGuidance.textContent = 'Detected image/photo media. Auto keeps the original format; MP4 and MP3 are unavailable.';
    } else {
      formatGuidance.textContent = 'Auto is recommended because the media type could not be confirmed. It lets yt-dlp choose a compatible source format.';
    }
  }

  function updateFormatAvailability(mediaType, hasAudio = null) {
    detectedMediaType = ['video', 'audio', 'image'].includes(mediaType) ? mediaType : 'unknown';
    detectedAudio = typeof hasAudio === 'boolean' ? hasAudio : null;
    hasPreviewedMedia = true;
    const mp4 = formatRadios.find((item) => item.value === 'mp4');
    const mp3 = formatRadios.find((item) => item.value === 'mp3');
    if (mp4) mp4.disabled = detectedMediaType === 'audio' || detectedMediaType === 'image';
    if (mp3) mp3.disabled = detectedMediaType === 'image' || (detectedMediaType === 'video' && detectedAudio === false);

    const selected = formatRadios.find((item) => item.checked);
    if (selected?.disabled) {
      const previous = selected.value;
      setFormat('auto');
      refreshOptions();
      const reason = previous === 'mp4'
        ? `MP4 is unavailable for detected ${detectedMediaType === 'audio' ? 'audio-only' : 'image'} media; Auto has been selected.`
        : 'MP3 is unavailable because no audio track was detected; Auto has been selected.';
      updateFormatGuidance(reason);
      return;
    }
    updateFormatGuidance();
  }

  function friendlyNetworkError(error) {
    if (error?.name === 'AbortError') return 'The request was cancelled before the file finished downloading.';
    if (error?.name === 'NotAllowedError' || error?.name === 'SecurityError') return 'Your browser blocked the automatic save. Allow downloads for this site or try another browser.';
    if (error instanceof TypeError) return 'The connection was interrupted. Check your internet connection and try again.';
    return error?.message || 'The request did not finish. Please try again.';
  }

  function filenameFromResponse(response) {
    const header = response.headers.get('Content-Disposition') || '';
    const encoded = header.match(/filename\*=UTF-8''([^;]+)/i);
    const quoted = header.match(/filename="((?:\\.|[^"])*)"/i);
    const plain = header.match(/filename=([^;]+)/i);
    let name = quoted ? quoted[1].replace(/\\(.)/g, '$1') : plain?.[1]?.trim();
    if (encoded) {
      try { name = decodeURIComponent(encoded[1]); } catch (_) { name = encoded[1]; }
    }
    if (name) name = name.replace(/[\\/:*?"<>|\u0000-\u001f]/g, '_').trim();
    return name || 'media-download';
  }

  async function readDownload(response) {
    const total = Number(response.headers.get('Content-Length')) || 0;
    const reader = response.body?.getReader();
    if (!reader) {
      downloadProgress.hidden = false;
      downloadProgress.removeAttribute('value');
      downloadProgressLabel.hidden = false;
      downloadProgressLabel.textContent = 'Receiving the file…';
      const blob = await response.blob();
      downloadProgress.value = 100;
      downloadProgressLabel.textContent = `${readableBytes(blob.size)} received`;
      return blob;
    }
    const chunks = [];
    let received = 0;
    downloadProgress.hidden = false;
    downloadProgressLabel.hidden = false;
    if (!total) downloadProgress.removeAttribute('value');
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      received += value.byteLength;
      if (total) {
        downloadProgress.value = Math.min(100, (received / total) * 100);
        downloadProgressLabel.textContent = `${readableBytes(received)} of ${readableBytes(total)}`;
      } else {
        downloadProgressLabel.textContent = `${readableBytes(received)} received`;
      }
    }
    if (total) downloadProgress.value = 100;
    return new Blob(chunks, { type: response.headers.get('Content-Type') || 'application/octet-stream' });
  }

  async function responseError(response) {
    let payload = {};
    try {
      if ((response.headers.get('Content-Type') || '').includes('application/json')) payload = await response.json();
    } catch (_) { /* Use the status-based fallback below. */ }
    const retryAfter = Number(response.headers.get('Retry-After') || payload.retry_after || 0);
    if (payload.error) return retryAfter && response.status === 429 && !/try again in/i.test(payload.error)
      ? `${payload.error} (retry in about ${retryAfter} seconds)`
      : payload.error;
    if (response.status === 413) return 'This request is too large. Remove the cookie file or try again without an upload.';
    if (response.status === 429) return `The downloader is busy or you have reached a request limit${retryAfter ? `. Try again in about ${retryAfter} seconds` : ''}.`;
    if (response.status >= 500) return 'The source or downloader could not complete the request. Try again later or use another public link.';
    return 'The request was not accepted. Check the link and selected options, then try again.';
  }

  const lockableControls = [...form.querySelectorAll('input:not([type="hidden"]), select')];
  const originalDisabled = new Map();
  function setFormBusy(busy) {
    if (busy) {
      lockableControls.forEach((control) => {
        originalDisabled.set(control, control.disabled);
        control.disabled = true;
      });
    } else {
      lockableControls.forEach((control) => {
        control.disabled = originalDisabled.get(control) || false;
      });
      originalDisabled.clear();
    }
    previewButton.disabled = busy;
    downloadButton.disabled = busy || !previewKey || currentKey() !== previewKey;
  }

  formatRadios.forEach((radio) => radio.addEventListener('change', refreshOptions));
  urlInput.addEventListener('input', () => {
    updateUrlActions();
    clearPreview();
  });
  urlInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      previewButton.click();
    }
  });
  urlInput.addEventListener('dragover', (event) => event.preventDefault());
  urlInput.addEventListener('drop', (event) => {
    event.preventDefault();
    const dropped = event.dataTransfer?.getData('text/uri-list') || event.dataTransfer?.getData('text/plain') || '';
    if (dropped.trim()) {
      const value = dropped.trim().split(/\r?\n/).find((line) => !line.startsWith('#')) || '';
      setUrlValue(value);
      if (/^https?:\/\//i.test(value)) previewButton.click();
      else showError('Drop a complete HTTP or HTTPS media link.');
    }
  });
  browserCookieProfile?.addEventListener('input', clearPreview);
  urlInput.addEventListener('paste', () => {
    setTimeout(() => {
      updateUrlActions();
      const pastedUrl = urlInput.value.trim();
      if (!/^https?:\/\//i.test(pastedUrl) || previewButton.disabled) return;
      previewButton.click();
    }, 0);
  });
  pasteLinkButton?.addEventListener('click', pasteFromClipboard);
  clearUrlButton?.addEventListener('click', () => {
    setUrlValue('');
    urlInput.focus?.();
  });
  form.querySelectorAll('select').forEach((control) => control.addEventListener('change', () => {
    if ([videoQuality, audioQuality].includes(control)) updateFormatGuidance();
    else clearPreview();
  }));
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
    const requestKey = currentKey();
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
      if (currentKey() !== requestKey) {
        showError('The link or cookie settings changed while the preview was loading. Preview the current request again.');
        return;
      }
      if (!response.ok) {
        const fallback = response.status === 413
          ? 'The request or cookie file exceeds the server upload limit.'
          : 'Could not read this media link.';
        throw new Error(result.error || fallback);
      }

      updateFormatAvailability(result.media_type, result.has_audio);
      document.getElementById('preview-title').textContent = result.title || 'Untitled media';
      const mediaLabel = {
        video: 'Video',
        audio: 'Audio only',
        image: 'Image / photo',
      }[result.media_type] || 'Media type unconfirmed';
      const details = [mediaLabel, result.extractor, result.uploader, formatDuration(result.duration)].filter(Boolean);
      document.getElementById('preview-details').textContent = details.join(' · ');
      if (result.thumbnail) {
        image.src = result.thumbnail;
        image.hidden = false;
      }
      preview.hidden = false;
      message.hidden = true;
      previewKey = requestKey;
      downloadButton.disabled = false;
    } catch (error) {
      showError(currentKey() !== requestKey
        ? 'The link or cookie settings changed while the preview was loading. Preview the current request again.'
        : friendlyNetworkError(error));
    } finally {
      previewButton.disabled = false;
      previewButton.textContent = 'Preview link';
    }
  });

  form.addEventListener('submit', async (event) => {
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
    event.preventDefault();
    const data = new FormData(form);
    data.set('quality', selectedQuality());
    setFormBusy(true);
    downloadButton.textContent = 'Preparing…';
    loadingText.textContent = 'Contacting the source and preparing your file…';
    downloadProgress.hidden = true;
    downloadProgress.value = 0;
    downloadProgressLabel.hidden = true;
    downloadProgressLabel.textContent = '';
    saveDownload.hidden = true;
    saveDownload.removeAttribute('href');
    if (downloadObjectUrl) URL.revokeObjectURL(downloadObjectUrl);
    downloadObjectUrl = '';
    loading.querySelector('.spinner').hidden = false;
    loading.hidden = false;
    message.hidden = true;
    let completedBlob = null;
    try {
      const response = await fetch('/download', {
        method: 'POST',
        body: data,
        headers: { Accept: 'application/json, application/octet-stream' },
      });
      if (!response.ok) throw new Error(await responseError(response));
      loadingText.textContent = 'Receiving the file…';
      completedBlob = await readDownload(response);
      if (!completedBlob.size) throw new Error('The server returned an empty file. Please try again.');
      const filename = filenameFromResponse(response);
      downloadObjectUrl = URL.createObjectURL(completedBlob);
      saveDownload.href = downloadObjectUrl;
      saveDownload.download = filename;
      saveDownload.textContent = `Save ${filename} now`;
      saveDownload.hidden = false;
      saveDownload.click();
      loading.querySelector('.spinner').hidden = true;
      loadingText.textContent = 'Transfer finished';
      downloadProgress.hidden = false;
      downloadProgress.value = 100;
      downloadProgressLabel.hidden = false;
      downloadProgressLabel.textContent = `${readableBytes(completedBlob.size)} received · save link ready`;
      showSuccess(`File received: ${filename}. If the automatic save did not start, use “Save ${filename} now” below. Check your browser’s Downloads list; the app cannot confirm a disk save.`);
    } catch (error) {
      if (completedBlob && downloadObjectUrl) {
        loading.querySelector('.spinner').hidden = true;
        loadingText.textContent = 'File received — ready for you to save';
        downloadProgress.hidden = false;
        downloadProgress.value = 100;
        downloadProgressLabel.hidden = false;
        downloadProgressLabel.textContent = `${readableBytes(completedBlob.size)} received`;
        saveDownload.hidden = false;
        showSuccess(`Your file is ready. Use “Save ${saveDownload.download} now” below to start the browser download.`);
      } else {
        loading.hidden = true;
        showError(friendlyNetworkError(error));
      }
    } finally {
      setFormBusy(false);
      downloadButton.textContent = 'Download media';
    }
  });

  refreshOptions();
  updateUrlActions();
})();
