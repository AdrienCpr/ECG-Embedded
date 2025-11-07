import sys
import time
from collections import deque
import numpy as np

# --- Dépendances série & plot ---
try:
    import serial
    import serial.tools.list_ports as list_ports
except Exception as e:
    print("❌ PySerial est requis. Installe-le avec:  pip install pyserial")
    raise

import matplotlib.pyplot as plt

# ---------- Paramètres ----------
PORT_PREFERER = "COM7"     # port préféré si connu (modifiable)
BAUDRATE = 115200
FS = 200                   # fréquence d'échantillonnage visée (Hz)
BUFFER_POINTS = 500       # points visibles sur le graphe

# Lissage
SMOOTH_MODE = "iir"        # 'iir' ou 'ma'
ALPHA = 0.20               # IIR : 0..1
MA_WINDOW = 7              # moyenne glissante
YMIN, YMAX = 0.0, 3.3      # échelle verticale (V)
# -------------------------------


def detect_port(port_pref=PORT_PREFERER):
    ports = list(list_ports.comports())
    if not ports:
        return None
    names = [p.device for p in ports]
    if port_pref in names:
        return port_pref
    for p in ports:
        desc = f"{p.device} - {p.description}".lower()
        if any(k in desc for k in ["mbed", "stlink", "usb serial", "arduino", "cp210", "ch340", "cdc"]):
            return p.device
    return ports[0].device


class Smoother:
    def __init__(self, mode="iir", alpha=0.2, ma_window=7):
        self.mode = mode
        self.alpha = max(0.0, min(1.0, alpha))
        self.ma_window = max(1, int(ma_window))
        self._y = None
        self._buf = deque(maxlen=self.ma_window)

    def push(self, x):
        if self.mode == "ma":
            self._buf.append(x)
            return sum(self._buf) / len(self._buf)
        if self._y is None:
            self._y = x
        else:
            self._y = self._y + self.alpha * (x - self._y)
        return self._y


def open_serial():
    port = detect_port()
    if not port:
        print("❌ Aucun port série détecté. Branche la carte puis réessaie.")
        sys.exit(1)
    try:
        ser = serial.Serial(port, BAUDRATE, timeout=0.1)
        print(f"✅ Connecté sur {port} @ {BAUDRATE} bauds")
        return ser
    except serial.SerialException as e:
        print(f"❌ Impossible d'ouvrir le port '{port}': {e}")
        sys.exit(1)


def setup_plot():
    plt.ion()
    fig, ax = plt.subplots(figsize=(10, 4))
    x = np.arange(BUFFER_POINTS)
    y = np.full(BUFFER_POINTS, np.nan)

    color = (0.08, 0.65, 0.08)
    line = ax.plot(x, y, lw=2, color=color, label="line")[0]
    points = ax.plot(x, y, linestyle="None", marker="o", markersize=4, color=color, alpha=0.9, label="points")[0]

    ax.set_title("ECG (lissé) - BPM: -- | Filtre: ON | Mode: line")
    ax.set_xlabel("Échantillons")
    ax.set_ylabel("Tension (V)")
    if YMIN is not None and YMAX is not None:
        ax.set_ylim(YMIN, YMAX)
    else:
        ax.set_ylim(0, 1)
    ax.set_xlim(0, BUFFER_POINTS - 1)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()

    # par défaut afficher uniquement le tracé connecté
    line.set_visible(True)
    points.set_visible(False)

    return fig, ax, line, points, x, y

def detect_bpm(data, fs):
    """Détecte les pics R et calcule le BPM de façon plus robuste."""
    if len(data) < fs:  # au moins 1 seconde
        return None

    arr = np.asarray(data, dtype=float)

    # élimination de la dérive lente par moyenne mobile courte
    win = max(1, int(0.2 * fs))
    baseline = np.convolve(arr, np.ones(win) / win, mode="same")
    sig = arr - baseline

    # seuil adaptatif
    med = np.median(sig)
    p90 = np.percentile(sig, 90)
    thresh = med + 0.35 * (p90 - med)

    # maxima locaux au-dessus du seuil
    cand = np.where(
        (sig[1:-1] > sig[:-2]) & (sig[1:-1] > sig[2:]) & (sig[1:-1] > thresh)
    )[0] + 1
    if cand.size < 2:
        return None

    # force distance minimale entre pics (évite double-comptage) : 0.3s
    min_dist = max(1, int(0.3 * fs))
    peaks = []
    for c in cand:
        if peaks and (c - peaks[-1]) < min_dist:
            # garder le pic le plus fort dans la fenêtre
            if sig[c] > sig[peaks[-1]]:
                peaks[-1] = c
        else:
            peaks.append(c)

    if len(peaks) < 2:
        return None

    # intervalles en secondes et filtrage des intervalles aberrants
    intervals = np.diff(peaks) / float(fs)
    intervals = intervals[(intervals >= 0.3) & (intervals <= 2.0)]
    if intervals.size == 0:
        return None

    bpm = 60.0 / np.median(intervals)
    if not (30 <= bpm <= 220):
        return None
    return round(bpm, 1)

def main():
    ser = open_serial()
    smoother = Smoother(mode=SMOOTH_MODE, alpha=ALPHA, ma_window=MA_WINDOW)
    fig, ax, line, points, x, y = setup_plot()

    filter_enabled = True
    display_mode = "line"  # "line" ou "points"

    last_bpm_update = time.time()
    bpm = None

    def update_title():
        status = "ON" if filter_enabled else "OFF"
        ax.set_title(f"ECG (lissé) - BPM: {bpm or '--'} | Filtre: {status} | Mode: {display_mode}")

    def on_key(event):
        nonlocal filter_enabled, display_mode
        if event.key is None:
            return
        key = event.key.lower()
        if key == "g":
            filter_enabled = not filter_enabled
            update_title()
            fig.canvas.draw_idle()
        elif key == "d":
            display_mode = "points" if display_mode == "line" else "line"
            line.set_visible(display_mode == "line")
            points.set_visible(display_mode == "points")
            update_title()
            fig.canvas.draw_idle()

    fig.canvas.mpl_connect("key_press_event", on_key)

    try:
        while True:
            if ser.in_waiting:
                try:
                    raw = ser.readline().decode(errors="ignore").strip()
                    if not raw:
                        continue
                    val = float(raw)
                except ValueError:
                    continue

                # appliquer ou bypasser le filtre
                val = smoother.push(val) if filter_enabled else val

                # faire défiler le buffer rapidement (remplace np.roll(..., out=y))
                y[:-1] = y[1:]
                y[-1] = val

                # mise à jour minimale des artistes
                line.set_ydata(y)
                points.set_ydata(y)

                # Calcul BPM toutes les 2 secondes (sur les valeurs valides)
                if time.time() - last_bpm_update > 2:
                    valid = y[~np.isnan(y)]
                    if len(valid) > FS * 2:
                        bpm_est = detect_bpm(valid.tolist(), FS)
                        if bpm_est:
                            bpm = bpm_est
                    last_bpm_update = time.time()
                    update_title()

                # pause courte pour event loop matplotlib
                plt.pause(0.001)
            else:
                time.sleep(0.002)

    except KeyboardInterrupt:
        pass
    finally:
        try:
            ser.close()
        except Exception:
            pass
        print("\nPort série fermé. Au revoir 👋")

if __name__ == "__main__":
    main()
