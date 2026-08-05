#!/usr/bin/env python3
"""
deck-review - annotate an HTML deck in the browser, send the comments back to Claude Code.

Serves an HTML deck on localhost with an annotation overlay spliced in on the way
out. The file on disk is never touched, so whatever you print to PDF stays clean.

  python3 review_server.py deck.html
  python3 review_server.py deck.html --port 7654 --timeout 3600

Click a slide to drop a pin, drag over a blank area to box a region, or select
text to comment on the exact words. Hit "Send to Claude" and this process writes
review-NNN.json next to the deck, prints it to stdout and exits 0 - which is the
signal Claude Code waits on.
"""

import argparse
import json
import os
import re
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --------------------------------------------------------------- overlay ----

OVERLAY_CSS = """
:root{--dr-rail:340px}
body.dr-on.dr-deck{padding:0 var(--dr-rail) 0 0 !important;background:#eef0f4 !important}
/* Document mode leaves the page's own layout intact and only reserves the rail. */
body.dr-on.dr-doc{padding-right:var(--dr-rail) !important}
#dr-doc{position:relative}
#dr-doc .dr-layer{z-index:40}
#dr-wrap{position:relative;margin:24px auto}
#dr-stage{position:absolute;top:0;left:0;width:1920px;transform-origin:0 0}
#dr-stage section.slide{cursor:crosshair}
#dr-stage section.slide.dr-shift{cursor:cell}
.dr-layer{position:absolute;inset:0;z-index:60;pointer-events:none}
.dr-badge{position:absolute;width:30px;height:30px;border-radius:50%;background:#7145fc;color:#fff;
  font:700 15px/30px ui-monospace,monospace;text-align:center;transform:translate(-50%,-50%);
  pointer-events:auto;cursor:pointer;box-shadow:0 2px 8px rgba(26,31,46,.35);border:2px solid #fff}
.dr-badge.sel{background:#d0562b;transform:translate(-50%,-50%) scale(1.15)}
.dr-rect{position:absolute;border:2px solid #7145fc;background:rgba(113,69,252,.10);border-radius:4px}
.dr-rect .dr-badge{top:0;left:0}
.dr-txt{position:absolute;background:rgba(113,69,252,.16);border-bottom:2px solid #7145fc;border-radius:2px}
#dr-marquee{position:fixed;border:2px dashed #7145fc;background:rgba(113,69,252,.08);
  z-index:99998;pointer-events:none;border-radius:4px;display:none}

/* ---- rail ---- */
#dr-rail{position:fixed;top:0;right:0;width:var(--dr-rail);height:100vh;background:#fff;
  border-left:1px solid #d6d3e0;display:flex;flex-direction:column;z-index:99999;
  font:14px/1.45 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#1a1f2e}
#dr-rail header{padding:16px 18px;border-bottom:1px solid #e9e7ef}
#dr-rail h1{font:600 15px/1.2 -apple-system,sans-serif;margin:0 0 4px;letter-spacing:-.01em}
#dr-rail .sub{font:11px/1.4 ui-monospace,monospace;color:#8b95a8;letter-spacing:.06em;
  text-transform:uppercase;word-break:break-all}
#dr-status{display:inline-flex;align-items:center;gap:6px;margin-top:7px;
  font:10px ui-monospace,monospace;letter-spacing:.08em;text-transform:uppercase;color:#1f8a5a}
#dr-status .dot{width:7px;height:7px;border-radius:50%;background:#1f8a5a}
#dr-status.off{color:#d0562b}
#dr-status.off .dot{background:#d0562b}
#dr-err{display:none;margin:10px 12px 0;padding:11px 13px;border-radius:9px;background:#faeae4;
  border:1px solid #edc0ac;color:#a33b17;font-size:12px;line-height:1.55}
#dr-err b{display:block;font-weight:700;margin-bottom:4px;font-size:12px}
#dr-err code{font-family:ui-monospace,monospace;background:#fff;border:1px solid #edc0ac;
  padding:1px 4px;border-radius:3px;font-size:11px;word-break:break-all}
#dr-hint{padding:12px 18px;font-size:12px;line-height:1.55;color:#666e82;background:#fbfaf8;
  border-bottom:1px solid #e9e7ef}
#dr-hint b{color:#1a1f2e;font-weight:600}
#dr-hint kbd{font:11px ui-monospace,monospace;background:#fff;border:1px solid #d6d3e0;
  border-radius:4px;padding:1px 5px}
#dr-list{flex:1;overflow-y:auto;padding:10px 12px}
#dr-empty{padding:26px 8px;text-align:center;color:#8b95a8;font-size:13px;line-height:1.6}
.dr-item{border:1px solid #e9e7ef;border-radius:10px;padding:10px 12px;margin-bottom:8px;cursor:pointer;
  background:#fff;transition:border-color .15s,box-shadow .15s}
.dr-item:hover{border-color:#b9aef8;box-shadow:0 2px 10px -4px rgba(63,46,140,.28)}
.dr-item.sel{border-color:#7145fc;box-shadow:0 0 0 2px rgba(113,69,252,.14)}
.dr-item .top{display:flex;align-items:center;gap:8px;margin-bottom:6px}
.dr-item .n{width:20px;height:20px;border-radius:50%;background:#7145fc;color:#fff;flex:0 0 auto;
  font:700 11px/20px ui-monospace,monospace;text-align:center}
.dr-item .meta{font:10px ui-monospace,monospace;color:#8b95a8;letter-spacing:.07em;text-transform:uppercase}
.dr-item .cat{font:10px ui-monospace,monospace;letter-spacing:.07em;text-transform:uppercase;
  background:#efe9ff;color:#4a25b5;border-radius:20px;padding:2px 8px;font-weight:700}
.dr-item .del{margin-left:auto;color:#8b95a8;border:0;background:0;cursor:pointer;font-size:15px;
  line-height:1;padding:2px 4px;border-radius:4px}
.dr-item .del:hover{color:#d0562b;background:#faeae4}
.dr-item .quote{font:11px/1.45 ui-monospace,monospace;color:#666e82;background:#f7f6fa;
  border-left:2px solid #d6d3e0;padding:5px 8px;border-radius:0 4px 4px 0;margin-bottom:6px;
  max-height:52px;overflow:hidden}
.dr-item .note{font-size:13px;line-height:1.45;color:#1a1f2e;white-space:pre-wrap}
#dr-foot{padding:12px;border-top:1px solid #e9e7ef;display:flex;flex-direction:column;gap:8px}
#dr-send{width:100%;padding:12px;border:0;border-radius:10px;color:#fff;font:600 14px -apple-system,sans-serif;
  cursor:pointer;background:linear-gradient(135deg,#7145fc,#f8adf0)}
#dr-send[disabled]{background:#d6d3e0;cursor:not-allowed}
#dr-copy{width:100%;padding:9px;border:1px solid #d6d3e0;border-radius:9px;background:#fff;
  color:#3d4557;font:500 12px -apple-system,sans-serif;cursor:pointer}
#dr-copy:hover{border-color:#8b95a8}

/* ---- composer ---- */
#dr-pop{position:fixed;z-index:100000;width:320px;background:#fff;border:1px solid #d6d3e0;
  border-radius:12px;box-shadow:0 18px 50px -12px rgba(26,31,46,.34);padding:12px;display:none;
  font:14px -apple-system,BlinkMacSystemFont,sans-serif}
#dr-pop .ph{font:10px ui-monospace,monospace;color:#8b95a8;letter-spacing:.08em;
  text-transform:uppercase;margin-bottom:8px}
#dr-pop .pq{font:11px/1.45 ui-monospace,monospace;color:#666e82;background:#f7f6fa;padding:6px 8px;
  border-radius:6px;margin-bottom:8px;max-height:66px;overflow:auto}
#dr-pop textarea{width:100%;height:82px;border:1px solid #d6d3e0;border-radius:8px;padding:8px 10px;
  font:14px/1.45 -apple-system,sans-serif;resize:vertical;color:#1a1f2e;box-sizing:border-box}
#dr-pop textarea:focus{outline:0;border-color:#7145fc;box-shadow:0 0 0 3px rgba(113,69,252,.15)}
#dr-cats{display:flex;flex-wrap:wrap;gap:5px;margin:8px 0}
.dr-cat{font:10px ui-monospace,monospace;letter-spacing:.06em;text-transform:uppercase;
  border:1px solid #d6d3e0;background:#fff;color:#666e82;border-radius:20px;padding:4px 9px;cursor:pointer}
.dr-cat.on{background:#efe9ff;border-color:#7145fc;color:#4a25b5;font-weight:700}
#dr-pop .row{display:flex;gap:7px;align-items:center}
#dr-pop .row .sp{flex:1;font:10px ui-monospace,monospace;color:#8b95a8}
#dr-pop button.go{background:#7145fc;color:#fff;border:0;border-radius:8px;padding:8px 15px;
  font:600 13px -apple-system,sans-serif;cursor:pointer}
#dr-pop button.no{background:#fff;color:#666e82;border:1px solid #d6d3e0;border-radius:8px;
  padding:8px 13px;font:500 13px -apple-system,sans-serif;cursor:pointer}

/* ---- done ---- */
#dr-done{position:fixed;inset:0;z-index:100001;background:rgba(251,250,248,.97);display:none;
  flex-direction:column;align-items:center;justify-content:center;text-align:center;
  font:-apple-system,BlinkMacSystemFont,sans-serif;color:#1a1f2e}
#dr-done h2{font:600 34px/1.2 -apple-system,sans-serif;letter-spacing:-.02em;margin:0 0 12px}
#dr-done p{font-size:16px;color:#3d4557;max-width:46ch;line-height:1.6;margin:0}
#dr-done .tick{width:64px;height:64px;border-radius:50%;background:linear-gradient(135deg,#7145fc,#f8adf0);
  color:#fff;font-size:32px;line-height:64px;margin-bottom:22px}
@media print{#dr-rail,#dr-pop,#dr-marquee,.dr-layer,#dr-done{display:none !important}}
"""

OVERLAY_JS = r"""
(function(){
  var DECK = window.__DR_DECK__, RAIL = 340, KEY = 'deckreview:' + DECK;
  var CATS = ['copy','layout','data','colour','cut it'];
  var comments = [], pending = null, selId = null, drag = null, stage, wrap, zoom = 1;
  var pinTimer = null;   /* debounce, so a 2nd/3rd click can become a selection */
  var MODE = 'deck';   /* 'deck' = section.slide pages · 'doc' = flowing document */

  function $(t, c){ var e = document.createElement(t); if(c) e.className = c; return e; }
  function esc(s){ return String(s).replace(/[&<>"]/g, function(m){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]; }); }

  /* ---------- units ------------------------------------------------------
     A "unit" is whatever a comment is anchored to. In a deck that is the
     slide. In a document there are no pages, so it is the nearest sensible
     block: a section with an id, a heading, a paragraph, a table. Anchoring
     to a block instead of the page keeps coordinates valid after reflow. */
  var DOC_BLOCK = 'section[id],article,section,figure,figcaption,table,pre,blockquote,' +
                  'ul,ol,li,dl,dt,dd,h1,h2,h3,h4,h5,h6,p,header,footer,aside,main,div[id]';

  function unitEl(el){
    if(!el || !el.closest) return null;
    if(MODE === 'deck') return el.closest('section.slide');
    var b = el.closest(DOC_BLOCK);
    if(b) return b;
    /* Nothing semantic matched (a bare wrapper div, or padding between blocks).
       Fall back to the nearest ancestor that actually occupies a box. */
    var n = el;
    while(n && n !== document.body && n.id !== 'dr-doc'){
      var d = getComputedStyle(n).display;
      if(d === 'block' || d === 'flex' || d === 'grid' || d === 'table'){
        var r = n.getBoundingClientRect();
        if(r.width > 0 && r.height > 0) return n;
      }
      n = n.parentElement;
    }
    return document.getElementById('dr-doc');
  }
  /* smallest block that fully contains a dragged rectangle */
  function unitContaining(el, L, T, R, B){
    var u = unitEl(el);
    while(u && u.id !== 'dr-doc'){
      var r = u.getBoundingClientRect();
      if(r.left <= L + 1 && r.top <= T + 1 && r.right >= R - 1 && r.bottom >= B - 1) return u;
      u = u.parentElement ? unitEl(u.parentElement) : null;
    }
    return u || document.getElementById('dr-doc');
  }
  /* Unit keys must be stable across a reload, or comments restored from
     localStorage lose their anchor. Derive them from the DOM, never a counter. */
  function unitKey(u){
    if(MODE === 'deck') return 'slide-' + u.dataset.drSlide;
    if(u.id && u.id !== 'dr-doc') return '#' + u.id;
    return 'path:' + pathOf(u, document.getElementById('dr-doc'));
  }
  function unitFind(key){
    if(!key) return null;
    if(MODE === 'deck') return document.querySelector('section.slide[data-dr-slide="' +
      String(key).replace('slide-', '') + '"]');
    var root = document.getElementById('dr-doc');
    if(key.charAt(0) === '#') return document.getElementById(key.slice(1)) || root;
    var p = key.slice(5);
    if(!p) return root;
    try { return root.querySelector(':scope > ' + p) || root; } catch(e){ return root; }
  }
  /* nearest preceding heading, so a doc comment says where it is in words */
  function headingFor(u){
    var h = u.matches && u.matches('h1,h2,h3,h4,h5,h6') ? u : null;
    if(!h && u.querySelector) h = u.querySelector('h1,h2,h3,h4,h5,h6');
    var n = u;
    while(!h && n){
      var p = n.previousElementSibling;
      while(p){
        if(p.matches('h1,h2,h3,h4,h5,h6')){ h = p; break; }
        var inner = p.querySelectorAll ? p.querySelectorAll('h1,h2,h3,h4,h5,h6') : [];
        if(inner.length){ h = inner[inner.length - 1]; break; }
        p = p.previousElementSibling;
      }
      n = n.parentElement;
      if(n === document.body) break;
    }
    return h ? h.textContent.replace(/\s+/g, ' ').trim().slice(0, 90) : null;
  }
  function unitMeta(u){
    if(MODE === 'deck') return { slide: parseInt(u.dataset.drSlide, 10), section: null };
    var sec = u.closest('section[id],[id]');
    return { slide: null, section: {
      id: (sec && sec.id) || null, heading: headingFor(u) || null,
      tag: u.tagName.toLowerCase() } };
  }
  function unitLabel(c){
    if(c.mode === 'deck' || c.slide) return 'S' + String(c.slide).padStart(2, '0');
    var s = c.section || {};
    return (s.id ? '#' + s.id : (s.heading || s.tag || 'doc')).slice(0, 22);
  }
  function norm(u, cx, cy){
    var r = u.getBoundingClientRect();
    return { x:+(((cx - r.left) / r.width) * 100).toFixed(2),
             y:+(((cy - r.top) / r.height) * 100).toFixed(2) };
  }
  function pct(u, L, T, W, H){
    var r = u.getBoundingClientRect();
    return { x:+(((L - r.left) / r.width) * 100).toFixed(2),
             y:+(((T - r.top) / r.height) * 100).toFixed(2),
             w:+((W / r.width) * 100).toFixed(2),
             h:+((H / r.height) * 100).toFixed(2) };
  }
  function pathOf(el, root){
    var parts = [];
    while(el && el !== root && el.nodeType === 1){
      var i = 1, sib = el;
      while(sib = sib.previousElementSibling) i++;
      parts.unshift(el.tagName.toLowerCase() + ':nth-child(' + i + ')');
      el = el.parentElement;
    }
    return parts.join(' > ');
  }

  /* ---------- layout ---------- */
  function fit(){
    if(MODE !== 'deck') return;          /* documents already reflow on their own */
    var avail = window.innerWidth - RAIL - 48;
    zoom = Math.min(1, avail / 1920);
    stage.style.transform = 'scale(' + zoom + ')';
    wrap.style.width  = (1920 * zoom) + 'px';
    wrap.style.height = (stage.scrollHeight * zoom) + 'px';
  }

  /* ---------- store ---------- */
  function save(){ try{ localStorage.setItem(KEY, JSON.stringify(comments)); }catch(e){} }
  function load(){
    try{ var v = localStorage.getItem(KEY); if(v) comments = JSON.parse(v) || []; }catch(e){ comments = []; }
  }
  function renumber(){ comments.forEach(function(c,i){ c.n = i + 1; }); }

  /* ---------- composer ---------- */
  var pop, popTxt, popCat;
  function openComposer(anchor, at){
    pending = anchor;
    popCat = null;
    popTxt.value = '';
    pop.querySelector('.ph').textContent =
      anchor.type.toUpperCase() + '  ·  ' +
      (anchor.slide ? 'SLIDE ' + anchor.slide : unitLabel(anchor).toUpperCase());
    var q = pop.querySelector('.pq');
    if(anchor.text){ q.style.display = 'block'; q.textContent = anchor.text; }
    else { q.style.display = 'none'; }
    Array.prototype.forEach.call(pop.querySelectorAll('.dr-cat'), function(b){ b.classList.remove('on'); });
    pop.style.display = 'block';
    var w = 320, h = pop.offsetHeight || 260;
    var L = Math.max(12, Math.min(at.x + 14, window.innerWidth - RAIL - w - 12));
    var T = Math.max(12, Math.min(at.y - 20, window.innerHeight - h - 12));
    pop.style.left = L + 'px'; pop.style.top = T + 'px';
    popTxt.focus();
  }
  function closeComposer(){ pop.style.display = 'none'; pending = null; }
  function commit(){
    if(!pending) return;
    var note = popTxt.value.trim();
    if(!note){ popTxt.focus(); return; }
    pending.note = note;
    pending.category = popCat || 'note';
    pending.id = 'c' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    comments.push(pending);
    renumber(); save(); closeComposer(); render();
    window.getSelection().removeAllRanges();
  }

  /* ---------- render ---------- */
  function layerIn(u){
    var l = u.querySelector(':scope > .dr-layer');
    if(!l){
      if(MODE !== 'deck' && getComputedStyle(u).position === 'static') u.style.position = 'relative';
      l = $('div', 'dr-layer'); u.appendChild(l);
    }
    return l;
  }
  function render(){
    Array.prototype.forEach.call(document.querySelectorAll('.dr-layer'), function(l){ l.innerHTML = ''; });
    comments.forEach(function(c){
      var sl = unitFind(c.unit || ('slide-' + c.slide));
      if(!sl) return;
      var layer = layerIn(sl), badge = $('div', 'dr-badge');
      badge.textContent = c.n;
      if(c.id === selId) badge.classList.add('sel');
      badge.onclick = function(ev){ ev.stopPropagation(); select(c.id); };
      if(c.type === 'region' && c.rect){
        var box = $('div', 'dr-rect');
        box.style.cssText = 'left:' + c.rect.x + '%;top:' + c.rect.y + '%;width:' +
          c.rect.w + '%;height:' + c.rect.h + '%';
        box.appendChild(badge); layer.appendChild(box);
      } else if(c.type === 'text' && c.rect){
        var hl = $('div', 'dr-txt');
        hl.style.cssText = 'left:' + c.rect.x + '%;top:' + c.rect.y + '%;width:' +
          c.rect.w + '%;height:' + c.rect.h + '%';
        layer.appendChild(hl);
        badge.style.cssText = 'left:' + c.rect.x + '%;top:' + c.rect.y + '%';
        layer.appendChild(badge);
      } else {
        badge.style.cssText = 'left:' + c.point.x + '%;top:' + c.point.y + '%';
        layer.appendChild(badge);
      }
    });
    renderRail();
  }
  function renderRail(){
    var list = document.getElementById('dr-list');
    if(!comments.length){
      list.innerHTML = '<div id="dr-empty">No comments yet.<br><br>' +
        'Click ' + (MODE === 'deck' ? 'a slide' : 'a block') + ' to pin one, drag over a blank ' +
        'area to box a region, or select text to comment on the exact words.</div>';
    } else {
      list.innerHTML = comments.map(function(c){
        return '<div class="dr-item' + (c.id === selId ? ' sel' : '') + '" data-id="' + c.id + '">' +
          '<div class="top"><span class="n">' + c.n + '</span>' +
          '<span class="meta">' + esc(unitLabel(c)) + ' · ' + c.type + '</span>' +
          '<span class="cat">' + esc(c.category) + '</span>' +
          '<button class="del" data-del="' + c.id + '" title="Delete">×</button></div>' +
          (c.text ? '<div class="quote">' + esc(c.text) + '</div>' : '') +
          '<div class="note">' + esc(c.note) + '</div></div>';
      }).join('');
    }
    var n = comments.length, btn = document.getElementById('dr-send');
    if(!n){ btn.textContent = 'Nothing to send'; btn.disabled = true; }
    else if(!alive){ btn.textContent = 'Server offline — copy JSON instead'; btn.disabled = true; }
    else { btn.textContent = 'Send ' + n + ' comment' + (n > 1 ? 's' : '') + ' to Claude'; btn.disabled = false; }
    document.getElementById('dr-copy').style.display = n ? 'block' : 'none';
  }
  function select(id){
    selId = id; render();
    var c = comments.filter(function(x){ return x.id === id; })[0];
    if(!c) return;
    var sl = unitFind(c.unit || ('slide-' + c.slide));
    if(sl) sl.scrollIntoView({ behavior:'smooth', block:'center' });
    var it = document.querySelector('.dr-item[data-id="' + id + '"]');
    if(it) it.scrollIntoView({ behavior:'smooth', block:'nearest' });
  }
  function del(id){
    comments = comments.filter(function(c){ return c.id !== id; });
    if(selId === id) selId = null;
    renumber(); save(); render();
  }

  /* ---------- payload ---------- */
  function payload(){
    return { deck: DECK, mode: MODE, sent_at: new Date().toISOString(),
             count: comments.length,
             comments: comments.map(function(c){
               return { n:c.n, mode:c.mode || MODE, slide:c.slide == null ? null : c.slide,
                        section:c.section || null, type:c.type, category:c.category,
                        note:c.note, text:c.text || null, path:c.path || null,
                        point:c.point || null, rect:c.rect || null };
             }) };
  }
  function done(msg){
    var d = document.getElementById('dr-done');
    d.querySelector('p').textContent = msg;
    d.style.display = 'flex';
  }
  /* Never use alert() here: it is suppressed in embedded browser panes, which makes a
     failed send look like "nothing happened". Errors go in the rail where they show. */
  function showErr(html){
    var e = document.getElementById('dr-err');
    e.innerHTML = html; e.style.display = 'block';
  }
  function hideErr(){ document.getElementById('dr-err').style.display = 'none'; }

  var alive = true;
  function setAlive(v){
    if(v === alive) return;
    alive = v;
    var s = document.getElementById('dr-status');
    s.className = v ? '' : 'off';
    s.querySelector('span').textContent = v ? 'server live' : 'server offline';
    if(!v){
      showErr('<b>The review server has stopped.</b>Your comments are safe in this tab. ' +
              'Ask Claude Code to reopen the review, then reload this page, or use ' +
              '<b>Copy JSON</b> below and paste it into the chat.');
    } else { hideErr(); }
    renderRail();
  }
  function heartbeat(){
    fetch('/ping', { cache:'no-store' })
      .then(function(r){ setAlive(r.ok); })
      .catch(function(){ setAlive(false); });
  }

  function send(){
    if(!comments.length) return;
    var btn = document.getElementById('dr-send'), n = comments.length;
    btn.disabled = true; btn.textContent = 'Sending…'; hideErr();
    fetch('/review', { method:'POST', headers:{'Content-Type':'application/json'},
                       body: JSON.stringify(payload()) })
      .then(function(r){ if(!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function(j){
        try{ localStorage.removeItem(KEY); }catch(e){}
        done(n + ' comment' + (n > 1 ? 's' : '') + ' written to ' +
             (j.file || 'the review file') +
             '. Claude Code has been woken up. You can close this tab.');
      })
      .catch(function(e){
        setAlive(false);
        btn.disabled = false; renderRail();
        showErr('<b>Could not send (' + esc(e.message) + ').</b>' +
                'The review server is not answering, so nothing was written. ' +
                'Your ' + n + ' comment' + (n > 1 ? 's are' : ' is') + ' still here. ' +
                'Hit <b>Copy JSON to clipboard</b> and paste it into Claude Code, ' +
                'or ask it to reopen the review and reload this page.');
      });
  }

  /* ---------- chrome ---------- */
  function buildRail(){
    var rail = $('aside'); rail.id = 'dr-rail';
    rail.innerHTML =
      '<header><h1>' + (MODE === 'deck' ? 'Deck review' : 'Doc review') + '</h1>' +
      '<div class="sub">' + esc(DECK) + '</div>' +
      '<div id="dr-status"><i class="dot"></i><span>server live</span></div></header>' +
      '<div id="dr-hint"><b>Click</b> ' + (MODE === 'deck' ? 'a slide' : 'anywhere') +
      ' to pin · <b>drag</b> a blank area to box · ' +
      '<b>select text</b> to quote it. <kbd>⇧</kbd>+drag forces a box over text. ' +
      '<kbd>⌘</kbd><kbd>↵</kbd> saves, <kbd>esc</kbd> cancels.</div>' +
      '<div id="dr-err"></div>' +
      '<div id="dr-list"></div>' +
      '<div id="dr-foot"><button id="dr-send"></button>' +
      '<button id="dr-copy">Copy JSON to clipboard</button></div>';
    document.body.appendChild(rail);
    document.getElementById('dr-send').onclick = send;
    document.getElementById('dr-copy').onclick = function(){
      var t = JSON.stringify(payload(), null, 2), b = this;
      navigator.clipboard.writeText(t).then(function(){
        b.textContent = 'Copied ✓';
        setTimeout(function(){ b.textContent = 'Copy JSON to clipboard'; }, 1600);
      });
    };
    document.getElementById('dr-list').addEventListener('click', function(e){
      var d = e.target.closest('[data-del]');
      if(d){ e.stopPropagation(); del(d.getAttribute('data-del')); return; }
      var it = e.target.closest('.dr-item');
      if(it) select(it.getAttribute('data-id'));
    });

    pop = $('div'); pop.id = 'dr-pop';
    pop.innerHTML = '<div class="ph"></div><div class="pq"></div>' +
      '<textarea placeholder="What should change here?"></textarea>' +
      '<div id="dr-cats">' + CATS.map(function(c){
        return '<button class="dr-cat" data-cat="' + c + '">' + c + '</button>'; }).join('') + '</div>' +
      '<div class="row"><button class="go">Add comment</button>' +
      '<button class="no">Cancel</button><span class="sp">⌘↵</span></div>';
    document.body.appendChild(pop);
    popTxt = pop.querySelector('textarea');
    pop.querySelector('.go').onclick = commit;
    pop.querySelector('.no').onclick = closeComposer;
    pop.addEventListener('click', function(e){
      var b = e.target.closest('.dr-cat'); if(!b) return;
      var on = b.classList.contains('on');
      Array.prototype.forEach.call(pop.querySelectorAll('.dr-cat'), function(x){ x.classList.remove('on'); });
      if(!on){ b.classList.add('on'); popCat = b.getAttribute('data-cat'); } else { popCat = null; }
    });
    popTxt.addEventListener('keydown', function(e){
      if(e.key === 'Enter' && (e.metaKey || e.ctrlKey)){ e.preventDefault(); commit(); }
      if(e.key === 'Escape'){ e.preventDefault(); closeComposer(); }
    });

    var m = $('div'); m.id = 'dr-marquee'; document.body.appendChild(m);
    var d = $('div'); d.id = 'dr-done';
    d.innerHTML = '<div class="tick">✓</div><h2>Sent to Claude Code</h2><p></p>';
    document.body.appendChild(d);
  }

  /* ---------- input ---------- */
  function wireInput(){
    var mq = document.getElementById('dr-marquee');
    stage.addEventListener('mousedown', function(e){
      if(e.button !== 0) return;
      if(e.target.closest('.dr-badge') || e.target.closest('#dr-pop') ||
         e.target.closest('#dr-rail')) return;
      var sl = unitEl(e.target); if(!sl) return;
      /* A pending pin from the previous click is cancelled: this is turning
         into a double or triple click, which the browser will make a selection. */
      if(pinTimer){ clearTimeout(pinTimer); pinTimer = null; }
      closeComposer();
      drag = { sl:sl, x:e.clientX, y:e.clientY, moved:false, shift:e.shiftKey, el:e.target };
    });
    window.addEventListener('mousemove', function(e){
      if(!drag) return;
      if(!drag.moved && (Math.abs(e.clientX - drag.x) > 6 || Math.abs(e.clientY - drag.y) > 6)) drag.moved = true;
      if(!drag.moved) return;
      var sel = window.getSelection();
      if(!drag.shift && sel && !sel.isCollapsed){ mq.style.display = 'none'; return; }
      mq.style.display = 'block';
      mq.style.left = Math.min(drag.x, e.clientX) + 'px';
      mq.style.top = Math.min(drag.y, e.clientY) + 'px';
      mq.style.width = Math.abs(e.clientX - drag.x) + 'px';
      mq.style.height = Math.abs(e.clientY - drag.y) + 'px';
    });
    window.addEventListener('mouseup', function(e){
      if(!drag) return;
      var d = drag; drag = null; mq.style.display = 'none';
      var sel = window.getSelection();
      var txt = (sel && !sel.isCollapsed) ? sel.toString().replace(/\s+/g, ' ').trim() : '';

      function anchor(u, extra){
        var m = unitMeta(u);
        return Object.assign({ mode:MODE, unit:unitKey(u), slide:m.slide, section:m.section }, extra);
      }
      if(pinTimer){ clearTimeout(pinTimer); pinTimer = null; }
      if(txt && !d.shift){
        var node = sel.anchorNode, el = node && node.nodeType === 3 ? node.parentElement : node;
        var r = sel.getRangeAt(0).getBoundingClientRect();
        var u = MODE === 'deck' ? (unitEl(el) || d.sl)
                                : unitContaining(el, r.left, r.top, r.right, r.bottom);
        openComposer(anchor(u, { type:'text', text:txt, path: pathOf(el, u),
          rect: pct(u, r.left, r.top, r.width, r.height) }), { x:e.clientX, y:e.clientY });
        return;
      }
      if(d.moved){
        var x0 = Math.min(d.x, e.clientX), y0 = Math.min(d.y, e.clientY);
        var w = Math.abs(e.clientX - d.x), h = Math.abs(e.clientY - d.y);
        if(w < 8 || h < 8) return;
        var u2 = MODE === 'deck' ? d.sl : unitContaining(d.el, x0, y0, x0 + w, y0 + h);
        openComposer(anchor(u2, { type:'region', rect: pct(u2, x0, y0, w, h) }),
          { x:e.clientX, y:e.clientY });
        return;
      }
      /* Hold the pin briefly. Opening it immediately focuses the textarea, which
         steals the selection and makes a double or triple click impossible. */
      var u3 = d.sl, cx = e.clientX, cy = e.clientY, tgt = d.el;
      pinTimer = setTimeout(function(){
        pinTimer = null;
        openComposer(anchor(u3, { type:'pin', path: pathOf(tgt, u3),
          point: norm(u3, cx, cy) }), { x:cx, y:cy });
      }, 260);
    });
    window.addEventListener('keydown', function(e){
      if(e.key === 'Escape') closeComposer();
      if(e.key === 'Shift') document.body.classList.add('dr-shift');
    });
    window.addEventListener('keyup', function(e){
      if(e.key === 'Shift') document.body.classList.remove('dr-shift');
    });
    window.addEventListener('resize', fit);
  }

  /* ---------- boot ---------- */
  function boot(){
    var slides = Array.prototype.slice.call(document.querySelectorAll('section.slide'));
    MODE = slides.length ? 'deck' : 'doc';
    document.body.classList.add('dr-on', 'dr-' + MODE);

    if(MODE === 'deck'){
      wrap = $('div'); wrap.id = 'dr-wrap';
      stage = $('div'); stage.id = 'dr-stage';
      slides[0].parentNode.insertBefore(wrap, slides[0]);
      wrap.appendChild(stage);
      slides.forEach(function(s, i){
        stage.appendChild(s);
        s.dataset.drSlide = i + 1;
        var l = $('div', 'dr-layer'); s.appendChild(l);
      });
    } else {
      /* Documents have no pages. Wrap whatever is in <body> so there is one
         stable root to listen on, and leave the document's own layout alone. */
      stage = $('div'); stage.id = 'dr-doc';
      var kids = Array.prototype.slice.call(document.body.childNodes);
      document.body.appendChild(stage);
      kids.forEach(function(n){
        if(n.nodeType === 1 && (n.id === 'dr-rail' || n.id === 'dr-pop')) return;
        stage.appendChild(n);
      });
    }
    buildRail(); wireInput(); load(); renumber(); fit(); render();
    setTimeout(fit, 250);
    setInterval(heartbeat, 4000);
    window.addEventListener('focus', heartbeat);
  }
  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
"""

# ---------------------------------------------------------------- server ----

STATE = {"received": None, "deck": "", "outfile": ""}


def inject(html: str, deck_name: str) -> str:
    block = (
        "<style id=\"dr-style\">" + OVERLAY_CSS + "</style>\n"
        "<script>window.__DR_DECK__=" + json.dumps(deck_name) + ";</script>\n"
        "<script>" + OVERLAY_JS + "</script>\n"
    )
    # Splice by index, never re.sub: the overlay JS contains backslash escapes
    # (\s, \d) which re would try to expand as replacement-template groups.
    last = None
    for m in re.finditer(r"</body\s*>", html, re.I):
        last = m
    if last:
        return html[:last.start()] + block + html[last.start():]
    return html + block


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # keep stdout clean for the task payload
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        raw = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            try:
                html = open(STATE["deck_path"], encoding="utf-8").read()
            except OSError as e:
                self._send(500, "cannot read deck: %s" % e, "text/plain")
                return
            self._send(200, inject(html, STATE["deck"]), "text/html; charset=utf-8")
        elif path == "/ping":
            self._send(200, json.dumps({"ok": True, "deck": STATE["deck"]}))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if self.path.split("?")[0] != "/review":
            self._send(404, json.dumps({"error": "not found"}))
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception as e:
            self._send(400, json.dumps({"error": "bad payload: %s" % e}))
            return
        with open(STATE["outfile"], "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        STATE["received"] = data
        self._send(200, json.dumps({"ok": True, "file": os.path.basename(STATE["outfile"])}))
        threading.Thread(target=STATE["httpd"].shutdown, daemon=True).start()


def free_port(preferred):
    for p in [preferred] + list(range(preferred + 1, preferred + 20)):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise SystemExit("deck-review: no free port near %d" % preferred)


def next_outfile(deck_path):
    d = os.path.dirname(os.path.abspath(deck_path))
    stem = os.path.splitext(os.path.basename(deck_path))[0]
    for i in range(1, 1000):
        p = os.path.join(d, "%s.review-%03d.json" % (stem, i))
        if not os.path.exists(p):
            return p
    raise SystemExit("deck-review: too many review files")


def main():
    ap = argparse.ArgumentParser(description="Annotate an HTML deck and send comments to Claude Code.")
    ap.add_argument("deck", help="path to the deck .html")
    ap.add_argument("--port", type=int, default=7654)
    ap.add_argument("--timeout", type=int, default=3600, help="seconds to wait before giving up")
    ap.add_argument("--out", help="explicit output json path")
    ap.add_argument("--no-open", action="store_true", help="do not open a browser")
    args = ap.parse_args()

    deck_path = os.path.abspath(args.deck)
    if not os.path.isfile(deck_path):
        raise SystemExit("deck-review: no such file: %s" % deck_path)

    port = free_port(args.port)
    STATE["deck_path"] = deck_path
    STATE["deck"] = os.path.basename(deck_path)
    STATE["outfile"] = os.path.abspath(args.out) if args.out else next_outfile(deck_path)

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    STATE["httpd"] = httpd
    url = "http://127.0.0.1:%d/" % port

    print("deck-review  %s" % STATE["deck"], flush=True)
    print("  url      %s" % url, flush=True)
    print("  writes   %s" % STATE["outfile"], flush=True)
    print("  waiting  up to %ds for you to hit Send to Claude" % args.timeout, flush=True)

    def watchdog():
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            if STATE["received"] is not None:
                return
            time.sleep(0.5)
        if STATE["received"] is None:
            print("\ndeck-review: timed out after %ds with no comments sent." % args.timeout, flush=True)
            httpd.shutdown()

    threading.Thread(target=watchdog, daemon=True).start()
    if not args.no_open:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\ndeck-review: interrupted, nothing sent.", flush=True)
        return 1
    finally:
        httpd.server_close()

    got = STATE["received"]
    if got is None:
        return 2
    print("\n=== REVIEW %s ===" % os.path.basename(STATE["outfile"]), flush=True)
    print(json.dumps(got, indent=2, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
