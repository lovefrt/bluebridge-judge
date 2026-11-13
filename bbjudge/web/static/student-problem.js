(function () {
  'use strict';

  var dataNode = document.getElementById('problem-data');
  var select = document.getElementById('problem');
  var detail = document.getElementById('problem-detail');
  if (!dataNode || !select || !detail) return;

  var problems = {};
  try {
    JSON.parse(dataNode.textContent || '[]').forEach(function (problem) {
      problems[problem.id] = problem;
    });
  } catch (error) {
    return;
  }

  function textBlock(text) {
    var block = document.createElement('div');
    block.className = 'problem-text-block';
    block.textContent = text;
    return block;
  }

  function renderSample(sample, index) {
    var item = document.createElement('div');
    item.className = 'sample-item';

    var title = document.createElement('div');
    title.className = 'sample-item-title';
    title.textContent = '示例 ' + (index + 1);
    item.appendChild(title);

    var inputLabel = document.createElement('div');
    inputLabel.className = 'sample-label';
    inputLabel.textContent = '输入';
    item.appendChild(inputLabel);

    var inputBox = document.createElement('pre');
    inputBox.className = 'sample-box';
    inputBox.textContent = sample.input || '';
    item.appendChild(inputBox);

    var outputLabel = document.createElement('div');
    outputLabel.className = 'sample-label';
    outputLabel.textContent = '输出';
    item.appendChild(outputLabel);

    var outputBox = document.createElement('pre');
    outputBox.className = 'sample-box';
    outputBox.textContent = sample.output || '';
    item.appendChild(outputBox);
    return item;
  }

  function render(problemId) {
    var problem = problems[problemId];
    detail.innerHTML = '';
    detail.classList.remove('muted');
    if (!problem) {
      detail.classList.add('muted');
      detail.textContent = '选择题目后显示题面。';
      return;
    }

    var description = document.createElement('div');
    description.className = 'problem-description';
    if (problem.description) {
      description.innerHTML = problem.description;
    } else {
      description.innerHTML = '<p class="muted">老师还没有填写题目描述。</p>';
    }
    detail.appendChild(description);

    if (problem.input_description) {
      var inputTitle = document.createElement('h4');
      inputTitle.textContent = '输入描述';
      detail.appendChild(inputTitle);
      detail.appendChild(textBlock(problem.input_description));
    }

    if (problem.output_description) {
      var outputTitle = document.createElement('h4');
      outputTitle.textContent = '输出描述';
      detail.appendChild(outputTitle);
      detail.appendChild(textBlock(problem.output_description));
    }

    if (problem.samples && problem.samples.length) {
      var section = document.createElement('div');
      section.className = 'sample-section';
      var title = document.createElement('h3');
      title.textContent = '输入输出样例';
      section.appendChild(title);
      problem.samples.forEach(function (sample, index) {
        section.appendChild(renderSample(sample, index));
      });
      detail.appendChild(section);
    }
  }

  select.addEventListener('change', function () {
    render(select.value);
  });
  render(select.value);
})();
