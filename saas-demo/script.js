const menuButton = document.querySelector('.menu-toggle');
const navigation = document.querySelector('.main-nav');
const toast = document.querySelector('#toast');

function showToast(message) {
  if (!toast) return;
  toast.textContent = message;
  toast.classList.add('show');
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove('show'), 2600);
}

if (menuButton && navigation) {
  menuButton.addEventListener('click', () => {
    const isOpen = menuButton.getAttribute('aria-expanded') === 'true';
    menuButton.setAttribute('aria-expanded', String(!isOpen));
    navigation.classList.toggle('open', !isOpen);
    document.body.classList.toggle('menu-open', !isOpen);
  });

  navigation.querySelectorAll('a').forEach((link) => {
    link.addEventListener('click', () => {
      menuButton.setAttribute('aria-expanded', 'false');
      navigation.classList.remove('open');
      document.body.classList.remove('menu-open');
    });
  });
}

const tabButtons = [...document.querySelectorAll('.tab-button')];
const tabPanels = [...document.querySelectorAll('.tab-panel')];
const paperType = document.querySelector('.paper-top small');
const paperTitle = document.querySelector('.paper-title h4');
const paperLabel = document.querySelector('.paper-title small');

const paperContent = {
  technical: ['Technical Documentation', 'Developer Reference', 'SALES PERFORMANCE · SEMANTIC MODEL'],
  audit: ['Audit & Health Report', 'Model Health Report', '86 / 100 · 12 RULES PASSED'],
  executive: ['Executive Summary', 'Leadership Brief', 'BUSINESS PURPOSE · OWNERSHIP · VALUE'],
  user: ['Business User Guide', 'Report Handbook', 'PAGES · FILTERS · KPI DEFINITIONS']
};

function activateDocumentTab(button) {
  const target = button.dataset.tab;
  tabButtons.forEach((item) => {
    const selected = item === button;
    item.classList.toggle('active', selected);
    item.setAttribute('aria-selected', String(selected));
    item.tabIndex = selected ? 0 : -1;
  });

  tabPanels.forEach((panel) => {
    const selected = panel.dataset.panel === target;
    panel.classList.toggle('active', selected);
    panel.hidden = !selected;
  });

  if (paperType && paperTitle && paperLabel && paperContent[target]) {
    paperType.textContent = paperContent[target][0];
    paperTitle.textContent = paperContent[target][1];
    paperLabel.textContent = paperContent[target][2];
  }
}

tabButtons.forEach((button, index) => {
  button.addEventListener('click', () => activateDocumentTab(button));
  button.addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    let nextIndex = index;
    if (event.key === 'ArrowRight') nextIndex = (index + 1) % tabButtons.length;
    if (event.key === 'ArrowLeft') nextIndex = (index - 1 + tabButtons.length) % tabButtons.length;
    if (event.key === 'Home') nextIndex = 0;
    if (event.key === 'End') nextIndex = tabButtons.length - 1;
    tabButtons[nextIndex].focus();
    activateDocumentTab(tabButtons[nextIndex]);
  });
});

const dialogs = [...document.querySelectorAll('.site-dialog')];
const selectedPlan = document.querySelector('#selected-plan');

document.querySelectorAll('[data-open-dialog]').forEach((trigger) => {
  trigger.addEventListener('click', () => {
    const dialog = document.querySelector(`#${trigger.dataset.openDialog}`);
    if (!dialog) return;
    if (selectedPlan && trigger.dataset.plan) selectedPlan.value = trigger.dataset.plan;
    dialog.showModal();
    document.body.classList.add('menu-open');
  });
});

document.querySelectorAll('[data-close-dialog]').forEach((button) => {
  button.addEventListener('click', () => {
    const dialog = button.closest('dialog');
    if (dialog) dialog.close();
  });
});

dialogs.forEach((dialog) => {
  dialog.addEventListener('click', (event) => {
    if (event.target === dialog) dialog.close();
  });
  dialog.addEventListener('close', () => document.body.classList.remove('menu-open'));
});

document.querySelectorAll('[data-demo-form]').forEach((form) => {
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    form.hidden = true;
    const success = form.parentElement.querySelector('.form-success');
    if (success) success.hidden = false;
  });
});

const sampleText = `PBICompass — Sales Performance Sample

TECHNICAL DOCUMENTATION
Generated from: Sales Performance.pbix

MODEL SUMMARY
Tables: 14
Measures: 128
Relationships: 23
Health score: 86 / 100

KEY MEASURE
Total Revenue YTD = CALCULATE([Total Revenue], DATESYTD('Date'[Date]))

INTERPRETATION
Revenue accumulated from the start of the year through the selected reporting date, while respecting active report filters.

DOCUMENT SET
01 Technical documentation
02 Audit and health report
03 Executive summary
04 Business user guide
`;

function downloadSample() {
  const blob = new Blob([sampleText], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = 'pbicompass-sales-performance-sample.txt';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
  showToast('Sample documentation downloaded.');
}

document.querySelector('#download-sample')?.addEventListener('click', downloadSample);
document.querySelector('#export-demo')?.addEventListener('click', downloadSample);

document.querySelectorAll('.product-sidebar button').forEach((button) => {
  button.addEventListener('click', () => {
    document.querySelectorAll('.product-sidebar button').forEach((item) => item.classList.remove('side-active'));
    button.classList.add('side-active');
    showToast(`${button.getAttribute('aria-label')} preview selected.`);
  });
});

document.querySelectorAll('.accordion details').forEach((detail) => {
  detail.addEventListener('toggle', () => {
    if (!detail.open) return;
    document.querySelectorAll('.accordion details').forEach((other) => {
      if (other !== detail) other.open = false;
    });
  });
});
