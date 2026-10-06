const overlay = document.querySelector('.loading-overlay');

document.querySelectorAll('.analyzing-form').forEach((form) => {
  form.addEventListener('submit', () => {
    if (form.checkValidity() && overlay) {
      overlay.hidden = false;
    }
  });
});

const commitInput = document.querySelector('#commits');
document.querySelectorAll('.commit-copy').forEach((button) => {
  button.addEventListener('click', () => {
    if (!commitInput) return;
    const hashes = commitInput.value.trim().split(/[\s,]+/).filter(Boolean);
    if (!hashes.includes(button.dataset.sha)) hashes.push(button.dataset.sha);
    commitInput.value = hashes.join('\n');
    commitInput.focus();
    button.blur();
  });
});
