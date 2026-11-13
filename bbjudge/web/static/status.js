(function () {
  'use strict';

  var sid = document.body.dataset.submissionId;
  if (!sid) return;

  var title = document.getElementById('status-title');
  var badge = document.getElementById('status-badge');
  var score = document.getElementById('score');
  var time = document.getElementById('time');
  var memory = document.getElementById('memory');
  var message = document.getElementById('message');
  var rows = document.getElementById('test-rows');
  var compileBlock = document.getElementById('compile-block');
  var compileOutput = document.getElementById('compile-output');
  var timer = null;

  var labels = {
    AC: '通过', WA: '答案错误', TLE: '超时', MLE: '内存超限',
    RE: '运行错误', CE: '编译错误', OLE: '输出超限', SE: '系统错误'
  };

  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
  }

  function formatNumber(value) {
    var n = Number(value);
    if (!isFinite(n)) return '—';
    return Math.abs(n - Math.round(n)) < 1e-9 ? String(Math.round(n)) : n.toFixed(1);
  }

  function statusOf(record) {
    if (record.status === 'done') {
      return { code: record.verdict || 'AC', label: labels[record.verdict] || record.verdict || '完成' };
    }
    if (record.status === 'error') return { code: 'SE', label: '系统错误' };
    if (record.status === 'judging') return { code: 'judging', label: '评测中' };
    return { code: 'queued', label: '排队中' };
  }

  function renderTests(result) {
    if (!result || !result.tests || !result.tests.length) {
      var empty = result ? '没有测试点（编译未通过）' : '等待评测…';
      rows.innerHTML = '<tr><td colspan="7" class="empty">' + empty + '</td></tr>';
      return;
    }
    rows.innerHTML = result.tests.map(function (test) {
      return '<tr>' +
        '<td>' + escapeHtml(test.index) + '</td>' +
        '<td>' + escapeHtml(test.name) + '</td>' +
        '<td><span class="badge ' + escapeHtml(test.verdict) + '">' + escapeHtml(test.verdict) + '</span></td>' +
        '<td>' + formatNumber(test.score) + ' / ' + formatNumber(test.max_score) + '</td>' +
        '<td>' + formatNumber(test.time_ms) + '</td>' +
        '<td>' + (Number(test.memory_kb || 0) / 1024).toFixed(1) + '</td>' +
        '<td class="msg">' + escapeHtml(test.message) + '</td>' +
        '</tr>';
    }).join('');
  }

  function render(record) {
    var state = statusOf(record);
    var result = record.result || null;
    var done = record.status === 'done';

    title.textContent = state.label;
    badge.textContent = state.code;
    badge.className = 'badge big ' + state.code;

    score.textContent = done
      ? formatNumber(record.score) + ' / ' + formatNumber(record.max_score)
      : '—';
    time.textContent = done ? formatNumber(record.time_ms) : '—';
    memory.textContent = done ? (Number(record.memory_kb || 0) / 1024).toFixed(1) : '—';
    message.textContent = record.message || '';

    renderTests(result);

    var compileText = result && result.compile_output;
    compileBlock.hidden = !compileText;
    if (compileText) compileOutput.textContent = compileText;
  }

  async function poll() {
    try {
      var response = await fetch('/api/submission/' + encodeURIComponent(sid), { cache: 'no-store' });
      if (!response.ok) return;
      var record = await response.json();
      render(record);
      if (record.status === 'done' || record.status === 'error') {
        if (timer) clearInterval(timer);
      }
    } catch (error) {
      /* 网络抖动时继续轮询 */
    }
  }

  poll();
  timer = setInterval(poll, 900);
})();
