'use strict';
document.querySelector('[data-copy-digest]')?.addEventListener('click', async () => {
  const text = document.querySelector('#digest-text').textContent;
  const status = document.querySelector('[data-copy-status]');
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const input = document.createElement('textarea');
      input.value = text;
      document.body.append(input);
      input.select();
      try {
        if (!document.execCommand('copy')) throw new Error('copy');
      } finally {
        input.remove();
      }
    }
    status.textContent = 'Đã sao chép';
  } catch {
    status.textContent = 'Hãy chọn văn bản bên dưới để sao chép.';
  }
});
