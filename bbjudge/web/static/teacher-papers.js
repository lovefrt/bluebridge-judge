(function () {
  'use strict';

  var form = document.getElementById('create-paper-form');
  var message = document.getElementById('paper-message');

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

  if (form) {
    form.addEventListener('submit', async function (event) {
      event.preventDefault();
      var formData = new FormData(form);
      var problemIds = formData.getAll('problem');
      if (!problemIds.length) {
        show('请至少选择一道题目', false);
        return;
      }
      var payload = {
        title: String(formData.get('title') || '').trim(),
        problem_ids: problemIds
      };
      show('创建中…', true);
      try {
        var response = await fetch('/api/papers', {
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

  document.querySelectorAll('[data-delete-paper]').forEach(function (button) {
    button.addEventListener('click', async function () {
      var paperId = button.getAttribute('data-delete-paper');
      if (!window.confirm('确定删除这份试卷吗？')) return;
      var url = '/api/papers/' + encodeURIComponent(paperId);
      try {
        var response = await fetch(url, { method: 'DELETE' });
        if (response.status === 409) {
          var blocked = await response.json().catch(function () { return {}; });
          var proceed = window.confirm(
            (blocked.error || '试卷正在被考试使用') +
            '\n\n是否强制删除？试卷会同时从相关考试中移除。'
          );
          if (!proceed) return;
          response = await fetch(url + '?force=1', { method: 'DELETE' });
        }
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
})();
