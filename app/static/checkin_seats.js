const params = new URLSearchParams(location.search);

const fromInput = document.getElementById('seat-from');
const toInput = document.getElementById('seat-to');
const loadBtn = document.getElementById('load-btn');
const printBtn = document.getElementById('print-btn');
const status = document.getElementById('sheet-status');
const grid = document.getElementById('seat-grid');

const sessionId = params.get('s') || '';
if (params.get('from')) fromInput.value = params.get('from');
if (params.get('to')) toInput.value = params.get('to');

function makeCard(seat, url) {
    const card = document.createElement('div');
    card.className = 'seat-card';

    const number = document.createElement('div');
    number.className = 'seat-card-number';
    number.textContent = 'Seat ' + seat;
    card.appendChild(number);

    const qr = qrcode(0, 'M');
    qr.addData(url);
    qr.make();
    const svg = new DOMParser().parseFromString(qr.createSvgTag({scalable: true, margin: 2}), 'image/svg+xml');
    card.appendChild(svg.documentElement);

    return card;
}

async function load() {
    if (!sessionId) {
        status.textContent = 'No session given. Open this page from the check-in Seat QR tab.';
        return;
    }
    const from = parseInt(fromInput.value, 10);
    const to = parseInt(toInput.value, 10);
    if (!Number.isInteger(from) || !Number.isInteger(to) || from < 1 || to < from) {
        status.textContent = 'Enter a valid seat range.';
        return;
    }

    loadBtn.disabled = true;
    status.textContent = 'Loading...';
    try {
        const resp = await fetch(
            `/admin/api/sessions/${sessionId}/checkin/seat-links?seat_from=${from}&seat_to=${to}`
        );
        const data = await resp.json();
        if (!resp.ok) {
            status.textContent = data.detail || 'Could not load the seat links.';
            grid.replaceChildren();
        } else {
            grid.replaceChildren(...data.seats.map(s => makeCard(s.seat, s.url)));
            status.textContent = `${data.seats.length} seats`;
        }
    } catch (e) {
        status.textContent = 'Connection problem, try again.';
    }
    loadBtn.disabled = false;
}

loadBtn.addEventListener('click', load);
printBtn.addEventListener('click', () => window.print());

if (sessionId) load();
else status.textContent = 'No session given. Open this page from the check-in Seat QR tab.';
