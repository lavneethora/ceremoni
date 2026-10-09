const sessionSelect = document.getElementById('session-select');
const printBtn = document.getElementById('print-btn');
const status = document.getElementById('sheet-status');
const grid = document.getElementById('card-grid');

const HONORS_LABELS = {honors: 'With honors', highest_honors: 'With highest honors'};

async function init() {
    const resp = await fetch('/admin/api/events');
    const events = await resp.json();
    for (const event of events) {
        for (const session of event.sessions) {
            if (!session.roster) continue;
            const opt = document.createElement('option');
            opt.value = session.id;
            opt.textContent = event.name + ' — ' + session.label;
            sessionSelect.appendChild(opt);
        }
    }

    // Honour ?s= so the dashboard can link straight to a session's cards
    const wanted = new URLSearchParams(location.search).get('s');
    if (wanted && [...sessionSelect.options].some(o => o.value === wanted)) {
        sessionSelect.value = wanted;
    } else if (sessionSelect.options.length === 2) {
        sessionSelect.selectedIndex = 1;
    }
    if (sessionSelect.value) await load();
}

function makeCard(card) {
    const el = document.createElement('div');
    el.className = 'student-card-print' + (card.code ? '' : ' no-audio');

    const name = document.createElement('div');
    name.className = 'name';
    name.textContent = card.typed_name;
    el.appendChild(name);

    if (card.major) {
        const meta = document.createElement('div');
        meta.className = 'meta';
        meta.textContent = card.major;
        el.appendChild(meta);
    }

    if (card.honors_level) {
        const honors = document.createElement('div');
        honors.className = 'honors';
        honors.textContent = HONORS_LABELS[card.honors_level] || '';
        el.appendChild(honors);
    }

    if (card.code) {
        const box = document.createElement('div');
        box.className = 'qr';
        const qr = qrcode(0, 'M');
        qr.addData(card.code);
        qr.make();
        const svg = new DOMParser().parseFromString(
            qr.createSvgTag({scalable: true, margin: 2}), 'image/svg+xml');
        box.appendChild(svg.documentElement);
        el.appendChild(box);
    } else {
        // No announcement for this student, so there is nothing to scan
        const note = document.createElement('div');
        note.className = 'read-aloud';
        note.textContent = 'No recording, read this name aloud';
        el.appendChild(note);
    }

    return el;
}

async function load() {
    const sessionId = sessionSelect.value;
    grid.replaceChildren();
    if (!sessionId) {
        status.textContent = 'Pick a session.';
        return;
    }

    status.textContent = 'Loading...';
    try {
        const resp = await fetch(`/admin/api/sessions/${sessionId}/cards`);
        const data = await resp.json();
        if (!resp.ok) {
            status.textContent = data.detail || 'Could not load the cards.';
            return;
        }
        if (sessionSelect.value !== sessionId) return;

        grid.replaceChildren(...data.cards.map(makeCard));
        const scannable = data.cards.filter(c => c.code).length;
        const missing = data.cards.length - scannable;
        status.textContent = `${data.cards.length} cards, ${scannable} scannable`
            + (missing ? `, ${missing} to read aloud` : '');
    } catch (e) {
        status.textContent = 'Connection problem, try again.';
    }
}

sessionSelect.addEventListener('change', load);
printBtn.addEventListener('click', () => window.print());

init();
