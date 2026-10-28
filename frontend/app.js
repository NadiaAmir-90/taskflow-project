
const $ = (selector) => document.querySelector(selector);

let tasks = [];
const MAX_HISTORY = 5;

let chatHistory = [];

try {
    chatHistory = JSON.parse(
        localStorage.getItem('taskflowChatHistory') || '[]'
    );

    if (!Array.isArray(chatHistory)) {
        chatHistory = [];
    }
} catch {
    chatHistory = [];
}
let currentFilter = 'all';
let editingId = null;
let pendingAction = null;
let candidateTaskIds = [];
let awaitingClarification = false;
let toastTimer;

const statusLabel = (status) => ({
    todo: 'To do',
    in_progress: 'In progress',
    done: 'Done'
}[status] || status);

const escapeHtml = (value = '') =>
    String(value).replace(/[&<>"']/g, ch => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
    }[ch]));

function toast(message) {
    const el = $('#toast');
    if (!el) return;

    el.textContent = message;
    el.classList.add('show');

    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
        el.classList.remove('show');
    }, 2600);
}

async function request(url, options = {}) {
    const response = await fetch(url, {
        headers: {
            'Content-Type': 'application/json',
            ...(options.headers || {})
        },
        ...options
    });

    if (!response.ok) {
        let message = `Request failed (${response.status})`;

        try {
            const data = await response.json();
            message = data.detail || message;
        } catch {
            // Keep the default error message.
        }

        throw new Error(message);
    }

    if (response.status === 204) return null;

    return response.json();
}

async function loadTasks() {
    try {
        tasks = await request('/api/tasks');
        renderTasks();
    } catch (error) {
        $('#task-list').innerHTML = `
            <div class="empty-state">
                Couldn't load tasks: ${escapeHtml(error.message)}
            </div>
        `;
    }
}

function renderTasks() {
    const filtered = tasks.filter(
        task => currentFilter === 'all' || task.status === currentFilter
    );

    $('#stat-total').textContent = tasks.length;

    $('#stat-progress').textContent = tasks.filter(
        task => task.status === 'in_progress'
    ).length;

    $('#stat-done').textContent = tasks.filter(
        task => task.status === 'done'
    ).length;

    $('#task-list').innerHTML = filtered.length
        ? filtered.map(task => `
            <article class="task-card ${task.status === 'done' ? 'is-done' : ''}">
                <input
                    class="task-check"
                    type="checkbox"
                    aria-label="Complete ${escapeHtml(task.title)}"
                    data-complete="${escapeHtml(task.id)}"
                    ${task.status === 'done' ? 'checked' : ''}
                >

                <div class="task-main">
                    <div class="task-title-row">
                        <h3 class="task-title">
                            ${escapeHtml(task.title)}
                        </h3>

                        <span class="pill priority-${escapeHtml(task.priority)}">
                            ${escapeHtml(task.priority)} priority
                        </span>
                    </div>

                    ${task.description ? `
                        <p class="task-description">
                            ${escapeHtml(task.description)}
                        </p>
                    ` : ''}

                    <div class="task-meta">
                        <span class="pill status-${escapeHtml(task.status)}">
                            ${escapeHtml(statusLabel(task.status))}
                        </span>

                        ${task.due_date ? `
                            <span class="due">
                                ◷ Due ${escapeHtml(task.due_date)}
                            </span>
                        ` : ''}
                    </div>
                </div>

                <div class="task-actions">
                    <button
                        class="icon-button"
                        data-edit="${escapeHtml(task.id)}"
                        title="Edit task"
                    >
                        Edit
                    </button>

                    <button
                        class="icon-button"
                        data-delete="${escapeHtml(task.id)}"
                        title="Delete task"
                    >
                        ×
                    </button>
                </div>
            </article>
        `).join('')
        : `
            <div class="empty-state">
                ${
                    currentFilter === 'all'
                        ? 'No tasks yet. Create one or ask Flow AI to get started.'
                        : 'No tasks in this view.'
                }
            </div>
        `;
}

function openForm(task = null) {
    editingId = task?.id || null;

    $('#form-title').textContent = task ? 'Edit task' : 'Create a task';
    $('#task-title').value = task?.title || '';
    $('#task-description').value = task?.description || '';
    $('#task-priority').value = task?.priority || 'medium';
    $('#task-status').value = task?.status || 'todo';
    $('#task-due').value = task?.due_date || '';

    $('#task-form').classList.remove('hidden');
    $('#task-title').focus();

    $('#task-form').scrollIntoView({
        behavior: 'smooth',
        block: 'nearest'
    });
}

function closeForm() {
    $('#task-form').classList.add('hidden');
    editingId = null;
}

function resetPendingChatState() {
    pendingAction = null;
    candidateTaskIds = [];
    awaitingClarification = false;
}

// CREATE AND EDIT TASKS

$('#new-task').addEventListener('click', () => openForm());

$('#cancel-form').addEventListener('click', closeForm);

$('#task-form').addEventListener('submit', async event => {
    event.preventDefault();

    const payload = {
        title: $('#task-title').value,
        description: $('#task-description').value,
        priority: $('#task-priority').value,
        status: $('#task-status').value,
        due_date: $('#task-due').value || null
    };

    const wasEditing = Boolean(editingId);

    try {
        if (editingId) {
            await request(`/api/tasks/${editingId}`, {
                method: 'PATCH',
                body: JSON.stringify(payload)
            });
        } else {
            await request('/api/tasks', {
                method: 'POST',
                body: JSON.stringify(payload)
            });
        }

        closeForm();
        await loadTasks();

        toast(wasEditing ? 'Task updated' : 'Task created');
    } catch (error) {
        toast(error.message);
    }
});

// TASK FILTERS

document.querySelectorAll('[data-filter]').forEach(button => {
    button.addEventListener('click', () => {
        document.querySelectorAll('[data-filter]').forEach(item => {
            item.classList.toggle('active', item === button);
        });

        currentFilter = button.dataset.filter;
        renderTasks();
    });
});

// EDIT AND DELETE TASKS

$('#task-list').addEventListener('click', async event => {
    const edit = event.target.closest('[data-edit]');
    const del = event.target.closest('[data-delete]');

    if (edit) {
        const task = tasks.find(item => item.id === edit.dataset.edit);

        if (task) openForm(task);
    }

    if (del) {
        const task = tasks.find(item => item.id === del.dataset.delete);

        if (!task) return;

        if (confirm(`Delete "${task.title}"? This cannot be undone.`)) {
            try {
                await request(`/api/tasks/${task.id}`, {
                    method: 'DELETE'
                });

                await loadTasks();
                toast('Task deleted');
            } catch (error) {
                toast(error.message);
            }
        }
    }
});

// COMPLETE OR REOPEN TASKS

$('#task-list').addEventListener('change', async event => {
    const checkbox = event.target.closest('[data-complete]');

    if (!checkbox) return;

    const task = tasks.find(item => item.id === checkbox.dataset.complete);

    if (!task) return;

    try {
        await request(`/api/tasks/${task.id}`, {
            method: 'PATCH',
            body: JSON.stringify({
                status: checkbox.checked ? 'done' : 'todo'
            })
        });

        await loadTasks();

        toast(
            checkbox.checked
                ? 'Task completed'
                : 'Task reopened'
        );
    } catch (error) {
        toast(error.message);
    }
});

// CHAT MESSAGE DISPLAY

function addMessage(text, who = 'assistant') {
    const row = document.createElement('div');

    row.className = `message ${
        who === 'user' ? 'user-message' : 'assistant-message'
    }`;

    if (who === 'assistant') {
        const avatar = document.createElement('div');
        avatar.className = 'mini-avatar';
        avatar.textContent = '✦';
        row.appendChild(avatar);
    }

    const bubble = document.createElement('div');
    bubble.className = 'bubble';

    // Use textContent to avoid rendering HTML from chat messages.
    bubble.textContent = text;

    row.appendChild(bubble);
    $('#chat-messages').appendChild(row);

    $('#chat-messages').scrollTop =
        $('#chat-messages').scrollHeight;
}

// SEND CHAT MESSAGE

async function sendChat(message) {
    message = message.trim();

    if (!message) return;

    addMessage(message, 'user');
    $('#chat-input').value = '';

    const send = $('#chat-form button');
    send.disabled = true;
    send.textContent = '…';

    try {
        const data = await request('/api/chat', {
            method: 'POST',
            body: JSON.stringify({
                message,
                history: chatHistory.slice(-MAX_HISTORY),
                pending_action: pendingAction,
                candidate_task_ids: candidateTaskIds,
                awaiting_clarification: awaitingClarification
            })
        });

        // Save the backend state so the next message can continue
        pendingAction = data.pending_action ?? null;
        candidateTaskIds = data.candidate_task_ids ?? [];
        awaitingClarification = data.awaiting_clarification ?? false;

        addMessage(
            data.reply || 'I could not process that request.'
        );

        chatHistory.push(
            { role: 'user', content: message },
            { role: 'assistant', content: data.reply || '' }
        );

        chatHistory = chatHistory.slice(-MAX_HISTORY);

        localStorage.setItem(
            'taskflowChatHistory',
            JSON.stringify(chatHistory)
        );

        

        if (data.tasks?.length) {
            for (const item of data.tasks) {
                const index = tasks.findIndex(
                    task => task.id === item.id
                );

                if (index >= 0) {
                    tasks[index] = item;
                } else {
                    tasks.unshift(item);
                }
            }

            await loadTasks();
        } else if (
            /deleted|cancelled|created|updated|couldn't find|could not find/i
                .test(data.reply || '')
        ) {
            await loadTasks();
        }

        // Clear selection state only after the backend has finished
        // or cancelled the operation. Preserve it while clarification
        // is still required.
        if (
            !awaitingClarification &&
            !pendingAction
        ) {
            candidateTaskIds = [];
        }
    } catch (error) {
        addMessage(`Sorry, something went wrong: ${error.message}`);
    } finally {
        send.disabled = false;
        send.textContent = '↑';
        $('#chat-input').focus();
    }
}

// CHAT FORM EVENTS

$('#chat-form').addEventListener('submit', event => {
    event.preventDefault();
    sendChat($('#chat-input').value);
});

$('#chat-input').addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        $('#chat-form').requestSubmit();
    }
});

document.querySelectorAll('[data-prompt]').forEach(button => {
    button.addEventListener('click', () => {
        sendChat(button.dataset.prompt);
    });
});

// INITIALIZE TASK BOARD

loadTasks();
