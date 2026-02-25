/* chats_export.js — Chat history export utilities */

function formatChatMarkdown(messages, title = 'Chat Export') {
  const lines = [`# ${title}`, '', '---', ''];
  (messages || []).forEach((msg) => {
    const role = msg.role === 'user' ? '**User**' : '**Assistant**';
    const content = (msg.content || '').trim();
    if (!content) return;
    lines.push(`### ${role}`, '', content, '');
  });
  return lines.join('\n');
}

function formatChatJSON(session) {
  return JSON.stringify(session, null, 2);
}

function formatChatText(messages) {
  const lines = [];
  (messages || []).forEach((msg) => {
    const role = msg.role === 'user' ? 'User' : 'Assistant';
    const content = (msg.content || '').trim();
    if (!content) return;
    lines.push(`${role}:`, content, '');
  });
  return lines.join('\n');
}

function downloadFile(filename, content, mimeType = 'text/plain') {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

function exportChatAsMarkdown(session, title) {
  const displayTitle = title || session.title || session.title_ai || 'chat';
  const sanitized = displayTitle.replace(/[^a-z0-9]/gi, '_').toLowerCase();
  const filename = `${sanitized}.md`;
  const content = formatChatMarkdown(session.messages, displayTitle);
  downloadFile(filename, content, 'text/markdown');
}

function exportChatAsJSON(session) {
  const displayTitle = session.title || session.title_ai || 'chat';
  const sanitized = displayTitle.replace(/[^a-z0-9]/gi, '_').toLowerCase();
  const filename = `${sanitized}.json`;
  const content = formatChatJSON(session);
  downloadFile(filename, content, 'application/json');
}

function exportChatAsText(session) {
  const displayTitle = session.title || session.title_ai || 'chat';
  const sanitized = displayTitle.replace(/[^a-z0-9]/gi, '_').toLowerCase();
  const filename = `${sanitized}.txt`;
  const content = formatChatText(session.messages);
  downloadFile(filename, content, 'text/plain');
}

// Export as global
window.ChatsExport = {
  formatChatMarkdown,
  formatChatJSON,
  formatChatText,
  downloadFile,
  exportChatAsMarkdown,
  exportChatAsJSON,
  exportChatAsText,
};
