(function () {
  'use strict';

  if (window.ProblemEditor) {
    window.ProblemEditor.init({
      editorId: 'edit-description',
      sampleListId: 'edit-sample-list',
      addBlankSample: true
    });
  }

  var form = document.getElementById('edit-problem-form');
  var message = document.getElementById('edit-message');
  if (!form) return;

  function show(text, ok) {
    if (!message) return;
    message.textContent = text;
    message.className = 'form-message ' + (ok ? 'ok' : 'err');
  }

  document.querySelectorAll('[data-delete-test]').forEach(function (button) {
    button.addEventListener('click', async function () {
      var index = button.getAttribute('data-delete-test');
      if (!window.confirm('确定删除第 ' + index + ' 个测试用例吗？对应的测试数据也会删除。')) {
        return;
      }
      try {
        var response = await fetch(
          '/api/problems/' + encodeURIComponent(form.dataset.pid) + '/tests/' + encodeURIComponent(index),
          { method: 'DELETE' }
        );
        var data = await response.json().catch(function () { return {}; });
        if (!response.ok) {
          show(data.error || '删除失败', false);
          return;
        }
        window.location.reload();
      } catch (error) {
        show('网络错误：' + error.message, false);
      }
    });
  });

  form.addEventListener('submit', async function (event) {
    event.preventDefault();
    var payload = {
      title: document.getElementById('edit-title').value.trim(),
      score: Number(document.getElementById('edit-score').value || 100),
      time_limit_ms: Number(document.getElementById('edit-time').value || 1000),
      memory_limit_mb: Number(document.getElementById('edit-memory').value || 256),
      comparator: document.getElementById('edit-comparator').value,
      description: document.getElementById('edit-description').innerHTML,
      input_description: document.getElementById('edit-input-description').value,
      output_description: document.getElementById('edit-output-description').value,
      samples: window.ProblemEditor
        ? window.ProblemEditor.collectSamples('edit-sample-list')
        : []
    };
    var answers = document.getElementById('edit-answers');
    if (answers) payload.answers = answers.value;

    show('保存中…', true);
    try {
      var response = await fetch('/api/problems/' + encodeURIComponent(form.dataset.pid), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      var data = await response.json().catch(function () { return {}; });
      if (!response.ok) {
        show(data.error || '保存失败', false);
        return;
      }
      var heading = document.querySelector('.page-head h1');
      if (heading) heading.textContent = payload.title;
      show('已保存', true);
    } catch (error) {
      show('网络错误：' + error.message, false);
    }
  });
})();
