/* RazMania Exhibitors — the little that has to be client-side.
 *
 * Everything on these pages is server-rendered. This file only (1) filters
 * the already-rendered directory grid as you type, and (2) runs the share
 * sheet on a profile. If it never loads, the pages are complete without it.
 */
(function () {
  'use strict';

  function track(name, detail) {
    try { window.dataLayer = window.dataLayer || []; window.dataLayer.push({ event: 'exhibitor_' + name, exhibitor: detail || '' }); } catch (e) {}
  }

  /* ------------------------------------------------------------ directory */
  var grid = document.querySelector('[data-grid]');
  if (grid) {
    var cards = Array.prototype.slice.call(grid.querySelectorAll('.rzx-card'));
    var search = document.querySelector('[data-search]');
    var chips = Array.prototype.slice.call(document.querySelectorAll('.rzx-chip'));
    var count = document.querySelector('[data-count]');
    var empty = document.querySelector('[data-empty]');
    var cat = '';

    function apply() {
      var q = (search && search.value || '').trim().toLowerCase();
      var shown = 0;
      cards.forEach(function (c) {
        var okQ = !q || (c.getAttribute('data-text') || '').indexOf(q) > -1;
        var okC = !cat || ('|' + (c.getAttribute('data-cats') || '') + '|').indexOf('|' + cat + '|') > -1;
        var on = okQ && okC;
        c.hidden = !on;
        if (on) shown++;
      });
      if (count) {
        var filtered = q || cat;
        count.hidden = !filtered;
        count.textContent = shown + ' of ' + cards.length + (cat ? ' in ' + cat : '') + (q ? ' matching “' + q + '”' : '');
      }
      if (empty) empty.hidden = shown > 0;
    }

    if (search) {
      ['input', 'change'].forEach(function (ev) { search.addEventListener(ev, apply); });
      // A URL like /exhibitors/?q=pokemon pre-fills the search.
      var m = location.search.match(/[?&]q=([^&]+)/);
      if (m) { search.value = decodeURIComponent(m[1].replace(/\+/g, ' ')); }
    }
    chips.forEach(function (b) {
      b.addEventListener('click', function () {
        cat = b.getAttribute('data-cat') || '';
        chips.forEach(function (x) { x.classList.toggle('is-on', x === b); });
        track('filter', cat || 'all');
        apply();
      });
    });
    apply();
  }

  /* -------------------------------------------------------------- profile */
  var page = document.querySelector('[data-profile]');
  if (!page) return;

  var name = page.getAttribute('data-name') || 'This exhibitor';
  var url = location.href.split('#')[0].split('?')[0];
  var sheet = page.querySelector('[data-sheet]');
  var toast = page.querySelector('[data-sheettoast]');
  var caption = name + ' at RazMania. Full profile: ' + url;

  function say(msg) { if (toast) { toast.textContent = msg; setTimeout(function () { toast.textContent = ''; }, 3200); } }
  function pop(u) { window.open(u, '_blank', 'noopener,width=620,height=560'); }
  function copy(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) return navigator.clipboard.writeText(text);
    return new Promise(function (res, rej) {
      var ta = document.createElement('textarea'); ta.value = text; ta.setAttribute('readonly', ''); ta.style.position = 'fixed'; ta.style.top = '-1000px';
      document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); res(); } catch (e) { rej(e); }
      document.body.removeChild(ta);
    });
  }
  function open() {
    if (!sheet) return;
    var nb = sheet.querySelector('[data-sh="native"]');
    if (nb && navigator.share) nb.hidden = false;
    sheet.hidden = false;
    track('share_open', name);
  }
  function close() { if (sheet) sheet.hidden = true; }

  Array.prototype.forEach.call(page.querySelectorAll('[data-share]'), function (b) { b.addEventListener('click', open); });
  if (sheet) {
    sheet.addEventListener('click', function (ev) { if (ev.target === sheet) close(); });
    var x = sheet.querySelector('[data-sheetclose]'); if (x) x.addEventListener('click', close);
    sheet.addEventListener('click', function (ev) {
      var b = ev.target.closest && ev.target.closest('[data-sh]'); if (!b) return;
      var k = b.getAttribute('data-sh');
      if (k === 'x') { pop('https://x.com/intent/post?text=' + encodeURIComponent(name + ' at RazMania') + '&url=' + encodeURIComponent(url)); track('share_x', name); }
      else if (k === 'fb') { pop('https://www.facebook.com/sharer/sharer.php?u=' + encodeURIComponent(url)); track('share_facebook', name); }
      else if (k === 'ig') { copy(caption).then(function () { say('Caption and link copied. Paste it into your Instagram post or story.'); }, function () { say('Could not copy. Long-press the link to share it.'); }); track('share_instagram', name); }
      else if (k === 'link') { copy(url).then(function () { say('Link copied'); }, function () { say('Could not copy'); }); track('share_copy_link', name); }
      else if (k === 'native' && navigator.share) { navigator.share({ title: name + ' at RazMania', text: caption, url: url }).then(function () { track('share_native', name); }).catch(function () {}); }
    });
  }
  document.addEventListener('keydown', function (ev) { if (ev.key === 'Escape') close(); });

  // Share kit: copy the pre-written caption.
  var capBtn = page.querySelector('[data-copycap]');
  var capText = page.querySelector('[data-caption]');
  var kitToast = page.querySelector('[data-kittoast]');
  if (capBtn && capText) {
    capBtn.addEventListener('click', function () {
      copy(capText.textContent.trim()).then(function () {
        if (kitToast) { kitToast.textContent = 'Caption copied. Paste it with the graphic.'; setTimeout(function () { kitToast.textContent = ''; }, 3200); }
      }, function () { if (kitToast) { kitToast.textContent = 'Could not copy. Select the caption and copy it.'; } });
      track('kit_caption', name);
    });
  }
  page.addEventListener('click', function (ev) {
    var a = ev.target.closest && ev.target.closest('[data-t]');
    if (a) track(a.getAttribute('data-t'), name);
  });
  track('view', name);
})();
