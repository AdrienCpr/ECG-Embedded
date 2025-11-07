import sys
import time
from collections import deque

# --- Dépendances série & plot ---
try:
    import serial
    import serial.tools.list_ports as list_ports
except Exception as e:
    print("❌ PySerial est requis. Installe-le avec:  pip install pyserial")
    raise

import matplotlib.pyplot as plt

# ---------- Paramètres ----------
PORT_PREFERER = "COM5"     # port préféré si connu (modifiable)
BAUDRATE = 115200
FS = 200                   # fréquence d'échantillonnage visée (Hz) côté micro
BUFFER_POINTS = 1000       # points visibles sur le graphe
# Lissage : choisissez 'iir' (par défaut) ou 'ma'
SMOOTH_MODE = "iir"        # 'iir' (exponentiel) ou 'ma' (moyenne glissante)
ALPHA = 0.20               # IIR : 0..1 (plus grand = plus lisse mais plus lent)
MA_WINDOW = 7              # Moyenne glissante: taille de fenêtre (impair conseillé)
YMIN, YMAX = 0.0, 3.3      # échelle verticale (V). Mets None,None pour auto-scale.
# -------------------------------


def detect_port(port_pref=PORT_PREFERER):
    """Retourne un port série utilisable (str) ou None."""
    ports = list(list_ports.comports())
    if not ports:
        return None
    # 1) Si le port préféré existe → on le prend
    names = [p.device for p in ports]
    if port_pref in names:
        return port_pref
    # 2) Essaie de trouver un port qui ressemble à un périphérique série USB
    for p in ports:
        desc = f"{p.device} - {p.description}".lower()
        if any(k in desc for k in ["mbed", "stlink", "usb serial", "arduino", "cp210", "ch340", "cdc"]):
            return p.device
    # 3) Sinon, prends le premier
    return ports[0].device


class Smoother:
    """Deux modes de lissage: IIR (exponentiel) et moyenne glissante."""
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
        # mode IIR (exponentiel)
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
        print("   Ouvre le Gestionnaire de périphériques → Ports (COM & LPT) pour vérifier le COM.")
        sys.exit(1)


def setup_plot():
    plt.ion()
    fig, ax = plt.subplots(figsize=(10, 4))
    line, = ax.plot([], [], lw=2)
    ax.set_title("ECG (lissé)")
    ax.set_xlabel("Échantillons")
    ax.set_ylabel("Tension (V)")
    if YMIN is not None and YMAX is not None:
        ax.set_ylim(YMIN, YMAX)
    else:
        ax.set_ylim(0, 1)  # sera auto-ajusté ensuite
    ax.set_xlim(0, BUFFER_POINTS)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    return fig, ax, line


def main():
    ser = open_serial()
    smoother = Smoother(mode=SMOOTH_MODE, alpha=ALPHA, ma_window=MA_WINDOW)

    data = deque(maxlen=BUFFER_POINTS)
    xs = list(range(BUFFER_POINTS))

    fig, ax, line = setup_plot()

    last_autoscale = time.time()
    t0 = time.time()
    n_ok = 0
    n_bad = 0

    try:
        while True:
            # Lecture non bloquante d'une ligne float
            if ser.in_waiting:
                try:
                    raw = ser.readline().decode(errors="ignore").strip()
                    if not raw:
                        continue
                    val = float(raw)  # tension en V (micro envoie déjà en V)
                    n_ok += 1
                except ValueError:
                    n_bad += 1
                    continue

                # Lissage
                val = smoother.push(val)

                data.append(val)

                # Mise à jour du tracé
                line.set_xdata(range(len(data)))
                line.set_ydata(list(data))

                # Auto-scale vertical doux toutes les 0.5 s si YMIN/YMAX = None
                if YMIN is None or YMAX is None:
                    if time.time() - last_autoscale > 0.5 and len(data) > 30:
                        ymin = min(data)
                        ymax = max(data)
                        pad = (ymax - ymin) * 0.15 if ymax > ymin else 0.2
                        ax.set_ylim(ymin - pad, ymax + pad)
                        last_autoscale = time.time()

                plt.pause(0.001)

            else:
                # petite sieste pour économiser le CPU
                time.sleep(0.002)

    except KeyboardInterrupt:
        pass
    finally:
        ser.close()
        dt = time.time() - t0
        print("\n--- Statistiques ---")
        print(f"Lignes valides : {n_ok}")
        print(f"Lignes ignorées : {n_bad}")
        if dt > 0:
            print(f"Débit moyen reçu ~ {n_ok/dt:.1f} Hz")
        print("Port série fermé. Au revoir 👋")


if __name__ == "__main__":
    main()