let currentSessionId = null;
let students = [];
let sortableInstance = null;
const sessionOptions = {};

const HONORS_LABELS = {honors: 'With honors', highest_honors: 'With highest honors'};

const sessionSelect = document.getElementById('session-select');
const playNextBtn = document.getElementById('play-next-btn');
const syncBtn = document.getElementById('sync-btn');
const resetBtn = document.getElementById('reset-btn');
const studentList = document.getElementById('student-list');
const nowPlaying = document.getElementById('now-playing');
const nowPlayingName = document.getElementById('now-playing-name');
const nowPlayingMeta = document.getElementById('now-playing-meta');
const audioPlayer = document.getElementById('audio-player');
const rosterTools = document.getElementById('roster-tools');
const announceStatus = document.getElementById('announce-status');
const importBtn = document.getElementById('import-btn');
const generateBtn = document.getElementById('generate-btn');
const rosterFile = document.getElementById('roster-file');

let statusTimer = null;

// Load events and sessions on page load
async function init() {
    const resp = await fetch('/admin/api/events');
    const events = await resp.json();

    // Clear options
    while (sessionSelect.options.length > 1) sessionSelect.remove(1);

    for (const event of events) {
        for (const session of event.sessions) {
            sessionOptions[session.id] = {roster: session.roster, order: session.order};
            const opt = document.createElement('option');
            opt.value = session.id;
            opt.textContent = event.name + ' — ' + session.label;
            sessionSelect.appendChild(opt);
        }
    }
}

function isSeatingSession() {
    return (sessionOptions[currentSessionId] || {}).order === 'seating';
}

function isRosterSession() {
    return (sessionOptions[currentSessionId] || {}).roster === true;
}

sessionSelect.addEventListener('change', async () => {
    currentSessionId = sessionSelect.value;
    clearTimeout(statusTimer);
    rosterTools.hidden = !isRosterSession();
    announceStatus.textContent = '';
    if (currentSessionId) {
        await loadStudents();
        playNextBtn.disabled = false;
    } else {
        studentList.replaceChildren(createEmptyState('Select a session to view students'));
        playNextBtn.disabled = true;
    }
});

async function loadStudents() {
    const resp = await fetch('/admin/api/students?session_id=' + currentSessionId);
    students = await resp.json();
    renderStudents();
    if (isRosterSession()) await refreshAnnounceStatus();
}

// Announcement clip progress for roster sessions; polls while a generation run is going
async function refreshAnnounceStatus() {
    clearTimeout(statusTimer);
    const sessionId = currentSessionId;
    const resp = await fetch(`/admin/api/sessions/${sessionId}/announcements/status`);
    if (!resp.ok || sessionId !== currentSessionId) return;
    const st = await resp.json();

    const needs = st.stale + st.missing;
    let text;
    if (st.total === 0) {
        text = 'No roster yet';
    } else if (st.running) {
        text = `Generating: ${st.ready} of ${st.total} ready`;
    } else if (needs === 0) {
        text = `All ${st.total} announcements ready`;
    } else {
        text = `${st.ready} of ${st.total} ready, ${needs} need generating`;
    }
    if (st.failed.length > 0) text += `, ${st.failed.length} failed`;
    if (st.no_recording > 0) text += `, ${st.no_recording} without a recording`;
    announceStatus.textContent = text;
    announceStatus.title = st.failed.map(f => `${f.name}: ${f.message}`).join('\n');
    generateBtn.disabled = st.running || st.total === 0;

    if (st.running) {
        statusTimer = setTimeout(async () => {
            await refreshAnnounceStatus();
            if (!generateBtn.disabled && currentSessionId === sessionId) await loadStudents();
        }, 3000);
    }
}

function createEmptyState(text) {
    const div = document.createElement('div');
    div.className = 'empty-state';
    div.textContent = text;
    return div;
}

function createHeader(text) {
    const header = document.createElement('div');
    header.className = 'student-group-header';
    header.textContent = text;
    return header;
}

function renderStudents() {
    studentList.replaceChildren();
    if (sortableInstance) {
        sortableInstance.destroy();
        sortableInstance = null;
    }

    if (students.length === 0) {
        studentList.appendChild(createEmptyState('No students in this session'));
        return;
    }

    const seating = isSeatingSession();
    const roster = isRosterSession();
    let currentCollege = '';
    let sawSeated = false;
    let sawUnseated = false;

    for (const s of students) {
        const unseated = seating && s.seat_position === null;

        if (seating) {
            if (!unseated && !sawSeated) {
                sawSeated = true;
                studentList.appendChild(createHeader('Seating order'));
            } else if (unseated && !sawUnseated) {
                sawUnseated = true;
                studentList.appendChild(createHeader('Not yet seated'));
            }
        } else if (s.college !== currentCollege) {
            currentCollege = s.college;
            studentList.appendChild(createHeader(currentCollege));
        }

        const row = document.createElement('div');
        row.className = 'student-row' + (s.played ? ' played' : '')
            + (seating ? ' seating' : '') + (unseated ? ' unseated' : '');
        row.dataset.id = s.id;

        const lead = document.createElement('span');
        if (seating) {
            lead.className = 'seat-number';
            lead.textContent = unseated ? '' : String(s.seat_position);
        } else {
            lead.className = 'drag-handle';
            lead.textContent = '::';
        }

        const name = document.createElement('span');
        name.className = 'name';
        name.textContent = s.typed_name;
        if (roster && s.has_recording === false) {
            const tag = document.createElement('span');
            tag.className = 'no-recording';
            tag.title = 'No voice recording, announced with the plain voice';
            tag.textContent = 'No recording';
            name.appendChild(tag);
        }

        const major = document.createElement('span');
        major.className = 'major';
        major.textContent = s.major || '';

        const status = document.createElement('span');
        let statusClass;
        let statusText;
        if (roster) {
            statusClass = s.announcement === 'ready' ? 'ready' : (s.announcement === 'stale' ? 'processing' : 'pending');
            statusText = s.announcement === 'ready' ? 'Ready' : (s.announcement === 'stale' ? 'Outdated' : 'Pending');
        } else {
            statusClass = s.has_audio ? 'ready' : (s.status === 'processing' ? 'processing' : 'pending');
            statusText = s.has_audio ? 'Ready' : (s.status === 'processing' ? 'Processing' : 'Pending');
        }
        status.className = 'status ' + statusClass;
        status.textContent = statusText;

        const reprocessBtn = document.createElement('button');
        reprocessBtn.className = 'btn-icon reprocess-btn';
        reprocessBtn.type = 'button';
        reprocessBtn.textContent = '↻';
        reprocessBtn.title = "Re-download and re-process this student's audio";
        reprocessBtn.addEventListener('click', async (e) => {
            e.stopPropagation();
            if (reprocessBtn.disabled) return;
            reprocessBtn.disabled = true;
            const prevStatusText = status.textContent;
            const prevStatusClass = status.className;
            status.className = 'status processing';
            status.textContent = 'Processing';
            try {
                const resp = await fetch(`/admin/api/students/${s.id}/reprocess`, {method: 'POST'});
                const data = await resp.json();
                if (data.status === 'ok') {
                    if (currentSessionId) await loadStudents();
                } else {
                    alert('Re-process failed: ' + (data.message || 'Unknown error'));
                    status.className = prevStatusClass;
                    status.textContent = prevStatusText;
                    reprocessBtn.disabled = false;
                }
            } catch (err) {
                alert('Re-process error: ' + err.message);
                status.className = prevStatusClass;
                status.textContent = prevStatusText;
                reprocessBtn.disabled = false;
            }
        });

        row.appendChild(lead);
        row.appendChild(name);
        row.appendChild(major);
        if (s.honors_level) {
            const honors = document.createElement('span');
            honors.className = 'honors-tag';
            honors.textContent = s.honors_level === 'highest_honors' ? 'Highest honors' : 'Honors';
            row.appendChild(honors);
        }
        row.appendChild(reprocessBtn);
        row.appendChild(status);
        studentList.appendChild(row);
    }

    // Drag reorder writes the global sort order, so it is only for college based sessions
    if (!seating) {
        sortableInstance = new Sortable(studentList, {
            handle: '.drag-handle',
            ghostClass: 'sortable-ghost',
            filter: '.student-group-header',
            onEnd: saveOrder,
        });
    }
}

async function saveOrder() {
    const rows = studentList.querySelectorAll('.student-row');
    const order = Array.from(rows).map((row, i) => ({
        id: row.dataset.id,
        sort_order: i + 1,
    }));

    await fetch('/admin/api/students/reorder', {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({order}),
    });
}

playNextBtn.addEventListener('click', async () => {
    const resp = await fetch('/admin/api/ceremony/next?session_id=' + currentSessionId);
    const data = await resp.json();

    if (data.done) {
        nowPlaying.style.display = 'block';
        const started = document.querySelector('.student-row.played');
        if (isSeatingSession() && !started) {
            nowPlayingName.textContent = 'No one is seated yet';
            nowPlayingMeta.textContent = '';
            return;
        }
        nowPlayingName.textContent = 'Ceremony complete';
        nowPlayingMeta.textContent = '';
        playNextBtn.disabled = true;
        return;
    }

    // Mark as played and get audio
    const playResp = await fetch('/admin/api/ceremony/play/' + data.id + '?session_id=' + currentSessionId, {method: 'POST'});
    if (!playResp.ok) {
        const err = await playResp.json().catch(() => ({}));
        alert(err.detail || 'Could not play this student');
        return;
    }
    const playData = await playResp.json();

    // Show now playing
    nowPlaying.style.display = 'block';
    nowPlayingName.textContent = data.typed_name;
    nowPlayingMeta.textContent = (data.college || '') + ' — ' + (data.major || '')
        + (data.honors_level ? ' — ' + HONORS_LABELS[data.honors_level] : '');

    // Highlight active row
    document.querySelectorAll('.student-row').forEach(row => row.classList.remove('active'));
    const activeRow = document.querySelector('.student-row[data-id="' + data.id + '"]');
    if (activeRow) {
        activeRow.classList.add('active');
        activeRow.classList.add('played');
    }

    // Play audio
    if (playData.audio_url) {
        audioPlayer.src = playData.audio_url;
        audioPlayer.play();
    }
});

syncBtn.addEventListener('click', async () => {
    syncBtn.disabled = true;
    syncBtn.textContent = 'Syncing...';
    try {
        const resp = await fetch('/admin/api/sync', {method: 'POST'});
        const data = await resp.json();
        if (data.status === 'ok') {
            syncBtn.textContent = data.synced > 0 ? `Synced ${data.synced} new` : 'No new responses';
            if (data.synced > 0 && currentSessionId) {
                await loadStudents();
            }
        } else {
            syncBtn.textContent = 'Sync failed';
            alert('Sync error: ' + (data.message || 'Unknown error'));
        }
    } catch (e) {
        syncBtn.textContent = 'Sync error';
    }
    setTimeout(() => {
        syncBtn.textContent = 'Sync Forms';
        syncBtn.disabled = false;
    }, 2000);
});

resetBtn.addEventListener('click', async () => {
    if (!currentSessionId) return;
    if (!confirm('Reset all played states for this session?')) return;

    await fetch('/admin/api/ceremony/reset?session_id=' + currentSessionId, {method: 'POST'});
    await loadStudents();
    nowPlaying.style.display = 'none';
    playNextBtn.disabled = false;
});

function describeImport(r) {
    const lines = [
        `Rows read: ${r.rows}`,
        `Added to the roster: ${r.added}, updated: ${r.updated}`,
        `Matched existing students: ${r.matched_existing}`,
    ];
    if (r.created_stubs.length > 0) {
        lines.push(`Created ${r.created_stubs.length} student(s) with no form submission (no recording): ${r.created_stubs.slice(0, 10).join(', ')}${r.created_stubs.length > 10 ? ', ...' : ''}`);
    }
    if (r.duplicates.length > 0) {
        lines.push(`Duplicate rows (the later row won): ${r.duplicates.map(d => `${d.row} repeats ${d.same_as_row}`).join('; ')}`);
    }
    if (r.rejected.length > 0) {
        lines.push(`Skipped ${r.rejected.length} row(s):`);
        for (const x of r.rejected.slice(0, 10)) lines.push(`  row ${x.row}: ${x.reason}`);
        if (r.rejected.length > 10) lines.push(`  and ${r.rejected.length - 10} more`);
    }
    return lines.join('\n');
}

importBtn.addEventListener('click', () => {
    if (currentSessionId) rosterFile.click();
});

rosterFile.addEventListener('change', async () => {
    const file = rosterFile.files[0];
    rosterFile.value = '';
    if (!file || !currentSessionId) return;

    importBtn.disabled = true;
    importBtn.textContent = 'Importing...';
    try {
        const body = new FormData();
        body.append('file', file);
        const resp = await fetch(`/admin/api/sessions/${currentSessionId}/roster/import`, {method: 'POST', body});
        const data = await resp.json();
        if (!resp.ok) {
            alert('Import failed: ' + (data.detail || 'Unknown error'));
        } else {
            alert(describeImport(data));
            await loadStudents();
        }
    } catch (e) {
        alert('Import error: ' + e.message);
    }
    importBtn.textContent = 'Import roster';
    importBtn.disabled = false;
});

generateBtn.addEventListener('click', async () => {
    if (!currentSessionId) return;
    generateBtn.disabled = true;
    try {
        const resp = await fetch(`/admin/api/sessions/${currentSessionId}/announcements/generate`, {method: 'POST'});
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            alert('Could not start generation: ' + (err.detail || 'Unknown error'));
        }
    } catch (e) {
        alert('Generate error: ' + e.message);
    }
    await refreshAnnounceStatus();
});

init();
