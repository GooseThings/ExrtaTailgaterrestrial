 # Microwave RF Imaging & Tracking with a Portable "Tailgater" Satellite Antenna

Originally created by Gabe Emerson / Saveitforparts (2023) — his [video demo](https://youtu.be/lVOTZxNCgTM)
explains the original concept and the hardware reverse-engineering that made this possible.

This fork is actively maintained by Goose ([N8GMZ](http://github.com/GooseThings/)). What started as a couple
of small fixes has grown into a real-time satellite tracker, a rebuilt imaging pipeline with interpolation,
and ongoing work on a browser-based control panel — so at this point consider it a continuation of Gabe's
original project rather than a lightly-patched copy. Full credit to Gabe for the original scanning code and
for figuring out the Tailgater's serial protocol in the first place; none of this exists without that work.


 ## **Introduction:**

This code controls a portable satellite antenna over USB using serial commands. 
The dish_scan.py program aims the dish to a selected portion of the sky and records
the RF signal strength. The dish_image.py program reads the resulting data and 
creates	a heatmap of the scanned area. Frequencies using stock Tailgater antenna 
hardware will be in the Ku band (~11ghz)

 - Added 3/27/2026 - dish_track.py will track a satellite across the sky using Keppler data pulled from the internet.

Please note that neither the original author nor the current maintainer are experts in Python, Linux,
satellites, or radio theory! This code is still experimental and amateur, and it will likely void any
warranty your Tailgater antenna may have. There are probably better, faster, and more efficient ways to do
some of the functions and calculations in the code. Feel free to fix, improve, or add to anything — pull
requests and issues are welcome.


 ## **Applications:**

- Imaging geostationary TV satellites
- Surveying an environment or room for microwave radiation
- Imaging an environment using ambient or reflected microwaves from Ku band source
- Imaging other wavelengths with a different feedhorn or LNB (not tested)
- Integration with an SDR and other antenna elements (not tested) 


 ## **Hardware Requirements:**

This code has been developed and tested with a Dish Network "Tailgater" portable
satellite antenna. Specifically, a 2014 version in an octagonal-ish enclosure 
with a USB "A" connector on the mainboard (located inside the enclosure, behind 
the dish reflector). There are many variations, models, and versions of this 
antenna, including Wallace, VuQube, Dish, King Controls, etc. There isn't a
consistent model numbering scheme, so the easiest way to identify the correct 
model is by opening the top and looking for a USB port. Other models have mini-USB
or other ports, and some appear to have jumpers for 9-pin serial. Gabe's original
testing covered a 2011 version with a mini-USB port (partial success) and a 2014
USB-A version (fully tested).

Gabe's original test unit uses firmware version "pragelato.h 704 2013-08-09 03:41:27Z rudrava".
Other versions may have different console commands available. For example, the 2011
firmware does not seem to include "azangle". To use such a system, you will need to
replace any "azangle" commands with a series of "aznudge" or "azim" commands. 

This code has been tested sucessfully on a range of Linux PCs, from 686-class using
a low-resource distro, to higher-end running a modern distribution. 


 ## **Notes on power supply:**

The USB connection only provides data to/from the dish. Power for the board, LNB, and 
motors comes from the coax "F" connector. This needs between 13-18V DC, center pin 
positive. Normally this is provided by a set-top box or satellite reciever. Power can
also be provided by an in-line injector for powered antennas, a meter such as V8 Finder,
or simply a DC adapter wired to a coax cable. Providing 13V will tell the LNB to use
vertical polarity and 18V will use horizontal polarity. 


 ## **Package Requirements:**

dish_scan.py uses the numpy, pyserial and regex packages. dish_image uses matplotlib. dish_track uses skyfield and requests, in addition to packages in dish_scan.
They can be installed individually or by running "pip install -r requirements.txt"


 ## **Setting up / testing Tailgater console:**

To connect to a Tailgater antenna with USB A port, you will need an A-to-A cable
(available online). 

You can check for proper connection by running "lsusb" on Linux. The dish should show
up as "Microchip Technology, Inc. CDC RS-232 Emulation Demo".

Run "dmesg | grep tty" to see which port the dish is using. This is usually something
like /dev/ttyACM0, although it can jump to ttyACM1 or ACM2 if the power or USB
connection are interrupted. If your Tailgater is NOT on /dev/ttyACM0 you will need to 
edit the `port=` line near the top of dish_scan.py to reflect the correct port. 
	
To connect to the serial console on the dish, run "screen /dev/ttyACM0" (or appropriate
port) on Linux, or use a Windows serial terminal to connect to the usb device (typically
com3 or similar). You will initially get a blank screen. Typing "help" should return a
menu of available commands and a "GO>" prompt. 
	
Note that the console does not accept backspace, so if you make a mistake while typing,
just hit enter to clear the console. If necessary, close the console or unplug the 
dish to avoid a motor overrun. 


 ## **Positioning the dish:**

The Tailgater dish uses a 360-degree counter-clockwise coordinate system, with the coax
/ F connector as "North" / 0 degrees. The dish considers an azimuth of 90 to be 90 degrees
counterclockwise from the coax jack (looking down at the dish from above). Azimuth 180 is
directly opposite the coax jack, and azimuth 270 is 90 degrees clockwise from the RF jack.
This is technically "backwards" from a standard compass heading. The code is written to
take this into account, but please note that image and array outputs will show azimuth in
the dish's reference plane, opposite of standard compass or orbital azimuth. 
		
A common setup is to place the dish with the coax connector facing due North (for scans of
the Southern sky), but you can place it in any orientation you want. The dish scans left to
right, incrementing up from the starting elevation. Remember the coordinate system is
"backwards" compared to standard compass headings.  


 ## **Running a scan:**

Once the dish is connected, powered, and ready on a USB port, run:
"python3 dish_scan.py"
You will be prompted for the starting and ending azimuth and elevation of your scan. 
You will also be prompted for the resolution (low or high). 
Valid azimuth range is 0-360, and valid elevations are 5-70 degrees (outside of these
values may overrun the motors). If you enter a value outside the valid range, the 
program will use the minimum or maximum as appropriate. The default values are from
azimuth 90 to 270 (West to East in the dish's coordinate system), and from elevation 5 
to 70 degrees. This covers most of the Southern sky (if the dish is placed with coax jack
aiming due North). A low-resolution scan with default values takes approximately 3.5 hours
due to the minimum rfwatch runtime of 1 second. A high-resolution scan with default values
could take up to 72 hours and is not recommended!

Scans of smaller azimuth/elevation ranges should take less time. Estimated scan time for
your parameters will be shown once the scan starts, and you will be asked to confirm
or cancel the scan. 
	
During the scan, the current azimuth, elevation, and signal strength will be displayed for
each dish position. A preview image, "result-<timestamp>.png" will be created and will 
update live during the scan. On Gnome Image Viewer, this file should automatically refresh as
it updates. It will be very small (x pixels equal to your scan's azimuth range, and y pixels
equal to your scan's elevation range). However, it should be enough to get an idea if the
scan is working. 

	
 ## **Generating an image from a scan:**
	
Once a scan has completed, you will have three output files with the same timestamp:

- "result-<timestamp>.png"         The low-resolution preview image.
	
- "raw-data-<timestamp>.txt"       The raw scan values in a numpy array.
	
- "scan-settings-<timestamp>.txt"  The scan parameters (start and end azimuth / elevation)
	
dish_image.py will use the two text files to create a heatmap of your scan. Run the code 
along with the name of the raw-data scan you want to process. For example:
"python3 dish_image.py raw-data-20230322-153935.txt"
	
The code will load the corresponding scan-settings file automatically, and opens a
heatmap of the scan in a new window. You can save this heatmap for later use. 


 ## **Example Images:**
A few example images are included to show what a scan looks like:
	 
- "dish_image example.png"  The result of running a default scan with dish_scan.py and 
processing with dish_image.py. Shows geostationary TV satellites.
				  
- "satellite overlay.png"   The previous file overlaid on a panoramic photo of the same area. 
Note that trees, roof overhangs, and power poles are visible in RF.
				  
- "satellite preview.png"   A scaled-up version of the "result" preview generated during a scan.
	
- "room overlay.png"        An overlay of a default scan run indoors, showing microwave RF
coming from a poorly-shielded PC tower (lower right). 
				  
- "house.png"		  A structure scan comparing KU band (with "hsv" colormap), visible
light, and 50% overlay of each. 
				  
- "tailgater.png"		  Example of the antenna unit used for this project.
		

 ## **Example Files**

A few example data files output by dish_scan.py are included, for processing with dish_image.py

- "raw-data-20230321-193653.txt":   numpy matrix of signal strength at each azimuth and elevation pair

- "scan-settings-20230321-193653.txt":    scan parameters for dish_image to use when processing

- "result-20230321-193653.png":     Preview image created by dish_scan (not used by dish_image)

## **Additional notes:**
	
The dish_scan.py code contains code for two resolution settings. Low resolution (default) uses
azangle and elangle commands on the Tailgater console, so each scan position is one degree. The
high-resolution version uses the "nudge" commands available in the Tailgater console. 
These "nudges" are not always consistent, so you may see banding or other image artifacts when
using it. High-res scans of smaller areas seem to have fewer errors. Scans of very large spatial
areas using the high-res scan may cause undue wear to your dish motors, as well as taking
a very long time to complete. 

The scan loop indexes forward on every elevation and returns the dish to the starting azimuth
between rows, rather than panning back and forth on alternate elevations. An earlier version
alternated direction to save time, but that caused indexing and gear-meshing issues that
distorted the resulting images. Always returning to the starting azimuth adds a little extra
time to the overall scan, but keeps the image clean.

The heatmap generated by dish_image.py uses the "inferno" colormap. If you wish to use another
colormap, change the `cmap=` argument in the `plt.imshow(...)` call near the bottom of the file.
"seismic" and "gnuplot2" also work well, though they can lose some definition on the background
landscape. "hsv" may also be useful for noisy scans. 
	
If you run into problems with this code, please open an issue on this repo rather than emailing —
it's much easier to track and follow up on.

 # dish_track.py
 This script will let you pick a satellite to track in real time.

 ## Install dependencies first:
```
pip install requests
pip install skyfield
pip install pyserial (If you haven't already installed it for dish_scan.py)
```
 ## How to use it:

 - Run ```python3 dish_track.py```
 - Click "Load" to fetch the current active-satellite TLE catalogue from Celestrak
 - Click "Connect" to open the dish's serial port (edit SERIAL_PORT below if it's not /dev/ttyACM0)
 - Select a satellite from the list and click "Track" — the dish will begin commanding azangle/elangle over serial, following the satellite across the sky. If the satellite is below the horizon it waits and starts moving the dish automatically when it rises.
 - Click "Stop" to stop tracking

 ## Key things to edit at the top of the file:
```
SERIAL_PORT  = '/dev/ttyACM0'  # your dish port
OBSERVER_LAT = 42.87           # your latitude
OBSERVER_LON = -85.68          # your longitude
UPDATE_INTERVAL = 2.0          # seconds between dish position updates
```
