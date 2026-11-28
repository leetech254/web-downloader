(() => {
  const form = document.getElementById('api-playground-form');
  if (!form) return;
  const format = document.getElementById('playground-format');
  const quality = document.getElementById('playground-quality');
  const submit = document.getElementById('playground-submit');
  const status = document.getElementById('playground-status');
  const output = document.getElementById('playground-response');

  function syncQualityOptions() {
    const audio = format.value === 'mp3';
    const options = audio
      ? [['320', 'MP3 320 kbps'], ['256', 'MP3 256 kbps'], ['192', 'MP3 192 kbps'], ['128', 'MP3 128 kbps']]
      : [['best', 'Best / source compatible'], ['2160', 'Up to 2160p'], ['1080', 'Up to 1080p'], ['720', 'Up to 720p'], ['480', 'Up to 480p']];
    quality.replaceChildren(...options.map(([value, label]) => {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = label;
      return option;
    }));
  }

  format.addEventListener('change', syncQualityOptions);
  syncQualityOptions();

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    submit.disabled = true;
    status.textContent = 'Sending a same-origin HTTPS request…';
    output.textContent = '';
    try {
      const response = await fetch('/api/info', {
        method: 'POST',
        headers: { Accept: 'application/json' },
        body: new FormData(form),
      });
      const payload = await response.json().catch(() => ({ error: 'The server returned a non-JSON response.' }));
      output.textContent = JSON.stringify(payload, null, 2);
      status.textContent = response.ok
        ? 'Request completed successfully.'
        : `Request failed with HTTP ${response.status}.`;
    } catch (error) {
      output.textContent = JSON.stringify({ error: error?.message || 'The request could not be completed.' }, null, 2);
      status.textContent = 'The request could not reach this HTTPS service.';
    } finally {
      submit.disabled = false;
    }
  });
})();
