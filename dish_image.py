# dish_image.py
# Tailgater scan visualizer with interpolation

import numpy as np
import sys
import os

# Optional: scipy for high-quality interpolation
try:
    from scipy.ndimage import zoom
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False


# ────────────────────────────────────────────────────────────────
# SETTINGS
# ────────────────────────────────────────────────────────────────

INTERPOLATION_FACTOR = 4   # Increase for smoother image (e.g. 2–8)


# ────────────────────────────────────────────────────────────────
# LOAD + PREPARE A SCAN FOR PLOTTING
# ────────────────────────────────────────────────────────────────

def load_scan(filepath, interpolation_factor=INTERPOLATION_FACTOR):
    """
    Load a raw-data-*.txt scan file and its matching scan-settings-*.txt
    file, clean and interpolate the data, and return everything needed
    to plot it: (interpolated_data, x_ticks, x_labels, y_ticks, y_labels,
    timestamp).
    """
    basename = os.path.basename(filepath)
    stem = os.path.splitext(basename)[0]

    # Filenames look like "raw-data-<date>-<time>.txt" - the timestamp is
    # always the last two '-'-separated segments, regardless of prefix.
    parts = stem.split('-')
    if len(parts) < 2:
        raise ValueError("Unexpected filename format")

    timestamp = parts[-2] + '-' + parts[-1]

    sky_data = np.loadtxt(filepath)

    settings_file = os.path.join(os.path.dirname(filepath), f"scan-settings-{timestamp}.txt")
    try:
        scan_params = np.loadtxt(settings_file)
    except OSError:
        raise FileNotFoundError(f"Missing scan settings file: {settings_file}")

    az_start, az_end, el_start, el_end, resolution = map(int, scan_params)

    if sky_data.shape[0] < 2 or sky_data.shape[1] < 2:
        raise ValueError("Data too small to process")

    cleaned_data = sky_data[1:, 1:]

    # Replace NaNs (if any)
    cleaned_data = np.nan_to_num(cleaned_data, nan=0.0)

    if interpolation_factor > 1:
        if SCIPY_AVAILABLE:
            # High-quality interpolation
            interpolated_data = zoom(
                cleaned_data,
                interpolation_factor,
                order=3  # cubic interpolation
            )
        else:
            interpolated_data = np.repeat(
                np.repeat(cleaned_data, interpolation_factor, axis=0),
                interpolation_factor, axis=1
            )
    else:
        interpolated_data = cleaned_data

    az_range_total = az_end - az_start
    el_range_total = el_end - el_start

    scale = interpolation_factor

    if resolution == 1:
        x = [0,
             (az_range_total - 1) * scale // 2,
             (az_range_total - 2) * scale]

        az_ticks = [az_end, (az_start + az_end) // 2, az_start]

        y = [0,
             (el_range_total - 1) * scale // 2,
             (el_range_total - 1) * scale]

        el_ticks = [el_end, (el_start + el_end) // 2, el_start]

    elif resolution == 2:
        AZ_SCALE = 5
        EL_SCALE = 3

        x = [0,
             ((az_range_total * AZ_SCALE) - 1) * scale // 2,
             ((az_range_total * AZ_SCALE) - 2) * scale]

        az_ticks = [az_end, (az_start + az_end) // 2, az_start]

        y = [0,
             ((el_range_total * EL_SCALE) - 1) * scale // 2,
             ((el_range_total * EL_SCALE)) * scale]

        el_ticks = [el_end, (el_start + el_end) // 2, el_start]

    else:
        raise ValueError(f"Unsupported resolution: {resolution}")

    return interpolated_data, x, az_ticks, y, el_ticks, timestamp


# ────────────────────────────────────────────────────────────────
# CLI ENTRY POINT
# ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import matplotlib.pyplot as plt

    if len(sys.argv) < 2:
        print("Usage: python dish_image.py <datafile>")
        sys.exit(1)

    print("Loading data file...")
    interpolated_data, x, az_ticks, y, el_ticks, timestamp = load_scan(sys.argv[1])

    plt.xticks(x, az_ticks)
    plt.yticks(y, el_ticks)

    print("Rendering heatmap...")
    plt.imshow(
        interpolated_data,
        cmap='inferno',
        origin='lower',
        aspect='auto'
    )

    plt.colorbar(location='bottom', label='RF Signal Strength')
    plt.xlabel("Azimuth (dish uses CCW heading)")
    plt.ylabel("Elevation")
    plt.title("Ku Band Scan " + timestamp)

    plt.tight_layout()
    plt.show()
