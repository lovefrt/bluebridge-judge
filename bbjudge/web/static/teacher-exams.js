(function () {
  'use strict';

  var form = document.getElementById('create-exam-form');
  var message = document.getElementById('exam-message');

  function show(text, ok) {
    if (!message) return;
    message.textContent = text;
    message.className = 'form-message ' + (ok ? 'ok' : 'err');
  }

  async function readError(response, fallback) {
    try {
      var data = await response.json();
      return data.error || fallback;
    } catch (error) {
      return fallback;
    }
  }

  async function post(url) {
    var response = await fetch(url, { method: 'POST' });
    if (!response.ok) {
      window.alert(await readError(response, '操作失败'));
      return false;
    }
    window.location.reload();
    return true;
  }

  if (form) {
    form.addEventListener('submit', async function (event) {
      event.preventDefault();
      var formData = new FormData(form);
      var paperIds = formData.getAll('paper');
      if (!paperIds.length) {
        show('请至少选择一套试卷', false);
        return;
      }
      var payload = {
        title: String(formData.get('title') || '').trim(),
        paper_ids: paperIds,
        duration_minutes: Number(formData.get('duration_minutes') || 60),
        scoreboard_enabled: formData.get('scoreboard_enabled') === 'on'
      };
      show('创建中…', true);
      try {
        var response = await fetch('/api/exams', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        if (!response.ok) {
          show(await readError(response, '创建失败'), false);
          return;
        }
        show('创建成功，正在刷新…', true);
        window.setTimeout(function () { window.location.reload(); }, 400);
      } catch (error) {
        show('网络错误：' + error.message, false);
      }
    });
  }

  document.querySelectorAll('[data-start-exam]').forEach(function (button) {
    button.addEventListener('click', function () {
      var examId = button.getAttribute('data-start-exam');
      if (window.confirm('开始作答后计时立即启动，确定吗？')) {
        post('/api/exams/' + encodeURIComponent(examId) + '/start');
      }
    });
  });

  document.querySelectorAll('[data-end-exam]').forEach(function (button) {
    button.addEventListener('click', function () {
      var examId = button.getAttribute('data-end-exam');
      if (window.confirm('确定提前结束这场考试吗？')) {
        post('/api/exams/' + encodeURIComponent(examId) + '/end');
      }
    });
  });

  document.querySelectorAll('[data-delete-exam]').forEach(function (button) {
    button.addEventListener('click', async function () {
      var examId = button.getAttribute('data-delete-exam');
      if (!window.confirm('确定删除这场考试吗？已有提交记录会保留。')) return;
      try {
        var response = await fetch('/api/exams/' + encodeURIComponent(examId), { method: 'DELETE' });
        if (!response.ok) {
          window.alert(await readError(response, '删除失败'));
          return;
        }
        window.location.reload();
      } catch (error) {
        window.alert('网络错误：' + error.message);
      }
    });
  });

  document.querySelectorAll('[data-scoreboard-exam]').forEach(function (checkbox) {
    checkbox.addEventListener('change', async function () {
      var examId = checkbox.getAttribute('data-scoreboard-exam');
      try {
        var response = await fetch('/api/exams/' + encodeURIComponent(examId) + '/scoreboard', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: checkbox.checked })
        });
        if (!response.ok) {
          window.alert(await readError(response, '切换失败'));
          checkbox.checked = !checkbox.checked;
          return;
        }
        var label = checkbox.parentElement.querySelector('span');
        if (label) label.textContent = checkbox.checked ? '已开启' : '已关闭';
      } catch (error) {
        window.alert('网络错误：' + error.message);
        checkbox.checked = !checkbox.checked;
      }
    });
  });
})();
