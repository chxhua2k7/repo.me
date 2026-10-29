// 分頁、iOS 相容性提示、複製 repo 網址。沒有 JS 時頁面一樣能讀（分頁全部展開）。
(function () {
  "use strict";

  var tabs = Array.prototype.slice.call(document.querySelectorAll("[data-tab]"));

  function show(id) {
    if (!tabs.some(function (t) { return t.dataset.tab === id; })) id = tabs[0].dataset.tab;
    tabs.forEach(function (t) {
      var on = t.dataset.tab === id;
      t.classList.toggle("active", on);
      t.setAttribute("aria-selected", on ? "true" : "false");
      var panel = document.getElementById(t.dataset.tab);
      if (panel) panel.hidden = !on;
    });
  }

  if (tabs.length) {
    tabs.forEach(function (t) {
      t.addEventListener("click", function (e) {
        e.preventDefault();
        show(t.dataset.tab);
        history.replaceState(null, "", "#" + t.dataset.tab);
      });
    });
    show(location.hash.slice(1));
  }

  var compat = document.getElementById("compat");
  var m = navigator.userAgent.match(/OS (\d+)_(\d+)(?:_(\d+))? like Mac OS X/);
  if (compat && m) {
    var ios = [+m[1], +m[2], +(m[3] || 0)];
    var parse = function (s) { return s ? s.split(".").map(Number) : null; };
    // n 給了就只比前 n 段：max "16" 代表整個 16.x
    var cmp = function (a, b, n) {
      n = n || Math.max(a.length, b.length);
      for (var i = 0; i < n; i++) {
        var d = (a[i] || 0) - (b[i] || 0);
        if (d) return d < 0 ? -1 : 1;
      }
      return 0;
    };
    var min = parse(compat.dataset.min);
    var max = parse(compat.dataset.max);
    var exclusive = compat.dataset.maxExclusive === "1";
    var state = "ok";
    if (min && cmp(ios, min) < 0) state = "tooOld";
    else if (max && (exclusive ? cmp(ios, max) >= 0 : cmp(ios, max, max.length) > 0)) state = "tooNew";
    var shown = ios[2] ? ios.join(".") : ios[0] + "." + ios[1];
    compat.textContent = compat.dataset[state].replace("{ios}", shown);
    compat.classList.add(state === "ok" ? "ok" : "bad");
    compat.hidden = false;
  }

  Array.prototype.forEach.call(document.querySelectorAll("[data-copy]"), function (button) {
    var label = button.textContent;
    button.addEventListener("click", function () {
      var done = function () {
        button.textContent = button.dataset.copied;
        setTimeout(function () { button.textContent = label; }, 1500);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(button.dataset.copy).then(done, function () {});
      } else {
        var field = document.createElement("textarea");
        field.value = button.dataset.copy;
        document.body.appendChild(field);
        field.select();
        try { document.execCommand("copy"); done(); } catch (e) {}
        document.body.removeChild(field);
      }
    });
  });
})();
