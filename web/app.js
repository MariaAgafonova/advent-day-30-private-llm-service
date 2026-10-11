'use strict';
const $ = id => document.getElementById(id);
let key = '', session = null, busy = false, connected = false, epoch = 0, before = null;
let sessions = [], controller = null;
const drafts = new Map();
const welcome = $('conversation').firstElementChild.cloneNode(true);

function showError(text) { $('error').textContent = text; $('error').hidden = !text; }
function headers() { return {'Authorization': `Bearer ${key}`, 'Content-Type': 'application/json'}; }
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: headers(), cache: 'no-store'});
  let data; try { data = await response.json(); } catch { throw new Error('Сервис вернул неполный ответ.'); }
  if (!response.ok) {
    const retry = response.headers.get('Retry-After');
    throw new Error((data.error?.message || 'Ошибка запроса.') + (retry ? ` Повторите через ${retry} с.` : ''));
  }
  return data;
}
function controls() {
  $('send').disabled = !connected || busy;
  $('message').disabled = !connected || busy;
  $('new-chat').disabled = !connected || busy;
  $('connect').disabled = busy;
  $('api-key').disabled = busy;
  for (const node of $('sessions').querySelectorAll('button')) node.disabled = busy;
}
function scrollDown() { $('conversation').scrollTop = $('conversation').scrollHeight; }

// Small safe Markdown subset: text nodes only; no HTML parsing, remote images or model links.
function inline(text, parent) {
  const parts = text.split(/(`[^`\n]+`|\*\*[^*\n]+\*\*)/g);
  for (const part of parts) {
    if (part.startsWith('`') && part.endsWith('`')) {
      const code = document.createElement('code'); code.textContent = part.slice(1, -1); parent.append(code);
    } else if (part.startsWith('**') && part.endsWith('**')) {
      const strong = document.createElement('strong'); strong.textContent = part.slice(2, -2); parent.append(strong);
    } else parent.append(document.createTextNode(part));
  }
}
function markdown(text, parent) {
  parent.replaceChildren();
  const parts = text.split(/```[^\n]*\n([\s\S]*?)```/g);
  parts.forEach((part, i) => {
    if (i % 2) { const pre = document.createElement('pre'); const code = document.createElement('code'); code.textContent = part; pre.append(code); parent.append(pre); }
    else inline(part, parent);
  });
}
function renderSources(refs, parent) {
  if (!refs?.length) return;
  const wrapper = document.createElement('div'); wrapper.className = 'sources';
  const title = document.createElement('span'); title.textContent = `Источники в контексте · ${refs.length}`; wrapper.append(title);
  for (const source of refs) {
    const details = document.createElement('details'), summary = document.createElement('summary');
    summary.textContent = `[${source.citation_id}] ${source.title || source.source}`; details.append(summary);
    const quote = document.createElement('blockquote'); quote.textContent = source.quote; details.append(quote);
    const location = document.createElement('div');
    location.textContent = `${source.source} · строки ${source.line_start}–${source.line_end}${source.truncated ? ' · фрагмент сокращён' : ''}`;
    details.append(location);
    if (source.source_url) {
      try { const url = new URL(source.source_url); if (['http:', 'https:'].includes(url.protocol)) {
        const a = document.createElement('a'); a.textContent = 'Открыть оригинал ↗'; a.href = url.href; a.target = '_blank'; a.rel = 'noopener noreferrer'; details.append(a);
      }} catch { /* Invalid document URL remains plain text. */ }
    }
    wrapper.append(details);
  }
  parent.append(wrapper);
}
function message(role, text, sources = [], metrics = {}, append = true) {
  const box = document.createElement('article'); box.className = `message ${role}`;
  const label = document.createElement('div'); label.className = 'message-label'; label.textContent = role === 'user' ? 'ВЫ' : 'ЛОКАЛЬНО';
  const body = document.createElement('div'); body.className = 'body'; markdown(text, body); box.append(label, body);
  renderSources(sources, box);
  if (metrics.total_ms !== undefined) { const stats = document.createElement('div'); stats.className = 'metrics'; stats.textContent = `${(metrics.total_ms / 1000).toFixed(1)} с · очередь ${(metrics.queue_ms / 1000).toFixed(1)} с${metrics.eval_count !== undefined ? ` · ${metrics.eval_count} токенов` : ''}${metrics.done_reason === 'length' ? ' · достигнут лимит ответа' : ''}`; box.append(stats); }
  if (append) $('conversation').append(box);
  return box;
}
function renderSessions() {
  $('sessions').replaceChildren(); $('session-count').textContent = sessions.length;
  for (const item of sessions) { const button = document.createElement('button'); button.textContent = item.title; button.title = item.title; button.classList.toggle('selected', item.id === session); button.disabled = busy; button.onclick = () => selectSession(item.id); $('sessions').append(button); }
}
async function refreshSessions(version = epoch) {
  const data = await api('/api/sessions'); if (version !== epoch) return;
  sessions = data.sessions; renderSessions();
}
async function selectSession(id) {
  if (busy) return;
  drafts.set(session || 'new', $('message').value);
  const version = ++epoch; session = id; before = null;
  busy = true; controls();
  $('message').value = drafts.get(id) || ''; $('conversation').replaceChildren(); showError(''); renderSessions();
  $('chat-title').textContent = sessions.find(s => s.id === id)?.title || 'Диалог';
  try {
    const data = await api(`/api/sessions/${id}/messages`); if (version !== epoch) return;
    for (const row of data.messages) message(row.role, row.content, row.sources, row.metrics);
    before = data.next_before; addLoadMore(version); scrollDown();
  } catch (err) { if (version === epoch) showError(err.message); }
  finally { if (version === epoch) { busy = false; controls(); } }
}
function addLoadMore(version) {
  if (!before) return;
  const button = document.createElement('button'); button.className = 'load-more'; button.textContent = 'Загрузить предыдущие сообщения';
  button.onclick = async () => {
    button.disabled = true;
    try { const data = await api(`/api/sessions/${session}/messages?before=${before}`); if (version !== epoch) return;
      const fragment = document.createDocumentFragment(); for (const row of data.messages) fragment.append(message(row.role, row.content, row.sources, row.metrics, false));
      button.remove(); $('conversation').prepend(fragment); before = data.next_before; addLoadMore(version);
    } catch (err) { showError(err.message); button.disabled = false; }
  }; $('conversation').prepend(button);
}
function newChat() {
  if (busy) return;
  drafts.set(session || 'new', $('message').value); epoch++; session = null; before = null;
  $('conversation').replaceChildren(welcome.cloneNode(true)); $('chat-title').textContent = 'Новый диалог';
  $('message').value = drafts.get('new') || ''; showError(''); renderSessions(); $('message').focus();
}
async function refreshStatus(version = epoch) {
  try {
    const data = await api('/api/status'); if (version !== epoch) return;
    connected = true;
    $('model-status').textContent = data.runtime.available ? `${data.runtime.model} · готова к работе` : 'Ollama недоступен';
    $('status-dot').classList.toggle('online', data.runtime.available);
    $('rag').disabled = !data.rag.available || busy; if (!data.rag.available) $('rag').checked = false;
    controls();
  } catch (err) { if (version !== epoch) return; connected = false; controls(); $('model-status').textContent = 'Нет подключения'; $('status-dot').classList.remove('online'); showError(err.message); }
}
$('connect-form').addEventListener('submit', async event => {
  event.preventDefault(); if (busy) return;
  key = $('api-key').value.trim(); $('api-key').value = ''; epoch++; session = null; sessions = []; drafts.clear();
  connected = false; controls();
  $('message').value = ''; $('conversation').replaceChildren(welcome.cloneNode(true)); $('chat-title').textContent = 'Новый диалог'; renderSessions(); showError('');
  const version = epoch; await refreshStatus(version);
  if (connected && version === epoch) { try { await refreshSessions(version); $('message').focus(); } catch (err) { showError(err.message); } }
});
$('new-chat').addEventListener('click', newChat);
$('message').addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); $('chat-form').requestSubmit(); } });
$('chat-form').addEventListener('submit', async event => {
  event.preventDefault(); if (busy || !connected) return;
  const text = $('message').value.trim(); if (!text) return;
  const draftId = session || 'new'; busy = true; controls(); $('rag').disabled = true; showError('');
  $('conversation').querySelector('.welcome')?.remove();
  const userBox = message('user', text), answerBox = message('assistant', '');
  const body = answerBox.querySelector('.body'); let answer = '', completed = false;
  controller = new AbortController(); $('activity').textContent = 'Отправка запроса…'; scrollDown();
  try {
    const response = await fetch('/api/chat', {method: 'POST', headers: headers(), signal: controller.signal,
      body: JSON.stringify({session_id: session, message: text, use_rag: $('rag').checked, stream: true})});
    if (!response.ok) { const data = await response.json(); const retry = response.headers.get('Retry-After'); throw new Error((data.error?.message || 'Ошибка запроса.') + (retry ? ` Повторите через ${retry} с.` : '')); }
    const reader = response.body.getReader(), decoder = new TextDecoder(); let buffer = '';
    const frame = data => {
      if (data.session_id) session = data.session_id;
      if (data.type === 'queued') $('activity').textContent = `В очереди · позиция ${data.position}`;
      if (data.type === 'generating') $('activity').textContent = 'Модель отвечает…';
      if (data.type === 'delta') { answer += data.text; markdown(answer, body); scrollDown(); }
      if (data.type === 'error') throw new Error(data.error.message);
      if (data.type === 'done') {
        completed = true; answerBox.replaceWith(message('assistant', data.answer, data.sources, data.metrics, false));
        $('message').value = ''; drafts.delete(draftId); drafts.delete(session); $('chat-title').textContent = sessions.find(s => s.id === session)?.title || text.slice(0, 70);
      }
    };
    while (true) { const {value, done} = await reader.read(); buffer += decoder.decode(value || new Uint8Array(), {stream: !done});
      let newline; while ((newline = buffer.indexOf('\n')) >= 0) { const line = buffer.slice(0, newline); buffer = buffer.slice(newline + 1); if (line.trim()) frame(JSON.parse(line)); }
      if (done) { if (buffer.trim()) frame(JSON.parse(buffer)); break; }
    }
    if (!completed) throw new Error('Соединение прервано. Сообщение сохранено в поле ввода; проверьте историю перед повтором.');
    await refreshSessions(); scrollDown();
  } catch (err) {
    controller?.abort(); showError(err.message);
    if (!completed) { userBox.remove(); answerBox.remove(); $('message').value = text; drafts.set(session || draftId, text); }
  } finally {
    busy = false; controller = null; $('activity').textContent = ''; controls(); await refreshStatus(); $('message').focus();
  }
});
setInterval(() => { if (key && !busy) refreshStatus(); }, 15000);
window.addEventListener('pagehide', () => { key = ''; controller?.abort(); });
