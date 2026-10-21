const form = document.querySelector('#registration-form');
const studentIdInput = document.querySelector('#student-id');
const nameInput = document.querySelector('#name');
const submitButton = document.querySelector('#submit-button');
const queryButton = document.querySelector('#query-button');
const resultPanel = document.querySelector('#result');
const resultTitle = document.querySelector('#result-title');
const resultMessage = document.querySelector('#result-message');
const resultName = document.querySelector('#result-name');
const resultRemaining = document.querySelector('#result-remaining');
const remainingDisplay = document.querySelector('#remaining');
const networkStatus = document.querySelector('#network-status');

const UNKNOWN_MESSAGE = '结果未确认，请刷新查询';
const PYTHON_WHITESPACE = '\u0009-\u000D\u001C-\u001F\u0020\u0085\u00A0\u1680\u2000-\u200A\u2028\u2029\u202F\u205F\u3000';
const PYTHON_EDGE_WHITESPACE = new RegExp(`^[${PYTHON_WHITESPACE}]|[${PYTHON_WHITESPACE}]$`, 'u');
const PYTHON_BLANK = new RegExp(`^[${PYTHON_WHITESPACE}]*$`, 'u');
let requestInFlight = false;

function hasPythonEdgeWhitespace(value) {
  return PYTHON_EDGE_WHITESPACE.test(value);
}

function setBusy(busy) {
  requestInFlight = busy;
  submitButton.disabled = busy;
  queryButton.disabled = busy;
  submitButton.setAttribute('aria-busy', String(busy));
}

function setNetwork(connected) {
  networkStatus.textContent = connected ? '已连接服务端' : '服务端连接失败';
  networkStatus.classList.toggle('offline', !connected);
}

function showResult(kind, title, message, name = '—', remaining = '—') {
  resultPanel.className = `result ${kind}`;
  resultTitle.textContent = title;
  resultMessage.textContent = message;
  resultName.textContent = name;
  resultRemaining.textContent = remaining === null || remaining === undefined ? '—' : String(remaining);
  remainingDisplay.textContent = typeof remaining === 'number' ? String(remaining) : '—';
}

function showUnknown() {
  showResult('unknown', UNKNOWN_MESSAGE, '请求可能已经到达服务端。不要直接重复提交，请先刷新查询。');
}

function showServerError(message) {
  showResult('error', '技术故障', message);
}

async function requestJson(url, options = {}) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal });
    const body = await response.json().catch(() => ({}));
    setNetwork(true);
    return { response, body };
  } catch (error) {
    if (error.name === 'AbortError') {
      setNetwork(false);
      showUnknown();
      return null;
    }
    setNetwork(false);
    showUnknown();
    return null;
  } finally {
    window.clearTimeout(timer);
  }
}

function handleRegistration(response, body) {
  const remaining = body.remaining;
  if (response.status === 201 && body.result === 'registered') {
    showResult('success', '报名成功', '服务端已保存这条报名记录。', body.name || nameInput.value, remaining);
    return;
  }
  if (body.result === 'already_registered') {
    showResult('duplicate', '已报名', '该 student_id 已有一条有效报名记录，未新增、未覆盖姓名。', body.name || '—', remaining);
    return;
  }
  if (body.result === 'full') {
    showResult('full', '名额已满', '本届报名名额已满，本次没有写入记录。', '—', remaining);
    return;
  }
  if (body.result === 'invalid_student_id' || body.result === 'invalid_name' || body.result === 'invalid_request') {
    showResult('invalid', '输入未通过', body.message || '请检查 student_id 和姓名。');
    return;
  }
  if (body.result === 'busy') {
    showResult('error', '服务繁忙', '本次未确认成功；请刷新查询后再决定是否重试。', '—', remaining);
    return;
  }
  showServerError('服务端未能给出可识别的结果。');
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (requestInFlight) return;
  const studentId = studentIdInput.value;
  const name = nameInput.value;
  if (studentId === '' || hasPythonEdgeWhitespace(studentId)) {
    showResult('invalid', '输入未通过', 'student_id 必须是非空字符串，且首尾不能有空白。');
    return;
  }
  if (name === '' || PYTHON_BLANK.test(name)) {
    showResult('invalid', '输入未通过', '姓名必须是非空字符串。');
    return;
  }
  setBusy(true);
  try {
    const result = await requestJson('/api/registrations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ student_id: studentId, name }),
    });
    if (result) handleRegistration(result.response, result.body);
  } finally {
    setBusy(false);
  }
});

queryButton.addEventListener('click', async () => {
  if (requestInFlight) return;
  const studentId = studentIdInput.value;
  if (studentId === '' || hasPythonEdgeWhitespace(studentId)) {
    showResult('invalid', '无法查询', '请先填写完整、首尾无空白的 student_id。');
    return;
  }
  setBusy(true);
  try {
    const result = await requestJson(`/api/registrations/${encodeURIComponent(studentId)}`);
    if (!result) return;
    const { response, body } = result;
    if (response.status !== 200) {
      showServerError(body.message || '查询失败。');
      return;
    }
    if (body.registered) {
      showResult('duplicate', '已报名', '服务端查询到有效报名记录。', body.name || '—', body.remaining);
    } else {
      showResult('idle', '尚未报名', '服务端没有查到该 student_id 的报名记录。', '—', body.remaining);
    }
  } finally {
    setBusy(false);
  }
});

window.addEventListener('load', () => {
  fetch('/api/health')
    .then((response) => {
      setNetwork(response.ok);
    })
    .catch(() => setNetwork(false));
});
