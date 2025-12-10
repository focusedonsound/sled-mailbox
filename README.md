# SLED: Santa's Letter Express Delivery

<p align="center">
  <a href="https://youtu.be/lKsuSaLmaug">
    <img src="docs/media/video-thumbnail.jpg"
         alt="Watch the SLED demo video on YouTube"
         width="640">
  </a>
</p> 

SLED is a smart "Letters to Santa" mailbox system powered by a Raspberry Pi, MQTT, and Home Assistant.

When someone drops a letter in the mailbox, SLED plays a video on a connected screen, sends events to Home Assistant, and counts car traffic passing by using dual radar sensors for direction detection.

## Features

- Idle video playback during show hours and event clip playback when a letter is detected.
- Dual radar sensors used to detect car direction and count inbound and outbound traffic.
- Tempeture sensor used to monitor outdoor temp... because why not.
- MQTT integration with Home Assistant using discovery, including:
  - Last letter timestamp
  - Last car timestamp
  - Last direction label
  - Total car count and per day counts.
- Designed to run as a systemd service on Raspberry Pi OS.

## Build supply list

This is the reference bill of materials for the physical SLED mailbox enclosure.  
Electronics are listed separately in the Hardware section.

### Overall dimensions

Birdhouse style enclosure sized to hold a 24″ monitor and internal frame:

- **Front / Back panels:** 22″ wide × 62″ tall (peak)
- **Side panels:** 8″ deep × 31″ lower wall + 23″ upper peak section
- **Bottom panel:** 22″ × 8″
- **Roof panels:** 24″ × 10″ (two pieces)

### Internal frame members

The internal frame is built from **pressure treated 2×4** lumber:

- **Corner posts:** lower and upper segments (approx. 31″ + 23″ per corner)
- **Ridge cleat:** 22″ (ties the peaks together)
- **Monitor brace:** 22″ (horizontal brace behind the monitor)
- **Bottom cleats:** 2 × 8″ (support the bottom panel)

### Materials (Veranda PVC build)

| Material                 | Size / Qty              | Notes                              |
|--------------------------|-------------------------|------------------------------------|
| Pressure treated 2×4     | 8 ft studs × 3–4 pcs    | Internal frame and cleats          |
| 1/4″ Veranda PVC sheet   | 4 ft × 8 ft × 1 sheet   | Main body panels (front / back / sides / bottom) |
| Additional PVC sheet     | Offcuts or second sheet | Roof panels and any trim pieces    |
| 3/4″ × 1-1/2″ PVC trim   | 8 ft × 2–4 pcs          | Optional internal cleats / trim    |
| Plexiglass / acrylic     | ~22″ × 14″              | Monitor window                     |
| Screws                   | #8 × 1-1/4″             | Stainless or coated, for PVC to wood |
| Exterior sealant         | —                       | Silicone or polyurethane caulk     |
| Exterior paint           | —                       | Santa red / white theme            |
| Hinges                   | 2 pcs                   | For rear or side access door       |
| Latch / magnetic catch   | 1 pc                    | Keeps the service door closed      |
| Cable glands             | 2 or more               | For power and sensor cabling       |
| Zip ties / anchors       | —                       | Internal cable management          |

The electronics for SLED (Raspberry Pi, display, sensors, power supplies) are covered in the Hardware section below.

## Build instructions

These steps describe roughly the physical build of the SLED enclosure using Veranda PVC sheet and a pressure treated 2×4 internal frame. Adjust dimensions to match your actual monitor and lumber cuts.

### 1. Cut the internal 2×4 frame

1. Cut four 2×4 **corner posts** based on your final height:
   - Lower section (approx. 31″)
   - Upper section (approx. 23″) to support the peaked roof area
2. Cut the **ridge cleat**:
   - 1 × 22″ 2×4 to span between the front and back peaks
3. Cut the **monitor brace**:
   - 1 × 22″ 2×4 to mount behind and support the 24″ monitor
4. Cut the **bottom cleats**:
   - 2 × 8″ 2×4 pieces to support the bottom panel front and back

Dry fit the frame so:

- The corner posts form a rectangle matching the 22″ × 8″ footprint.
- The ridge cleat ties the upper ends of the posts at the roof peak.
- The monitor brace sits at a height that centers the 24″ monitor in the front opening.

### 2. Assemble the 2×4 frame

1. Build the lower rectangular base from the bottom portions of the corner posts and bottom cleats.
2. Attach the upper post segments to reach the full height (31″ + 23″).
3. Install the ridge cleat between the front and back peaks.
4. Install the monitor brace between the side posts:
   - Ensure there is enough clearance behind the plexiglass window and room for the monitor’s depth.
5. Check that the frame is square and rigid.

This frame will carry the weight of the monitor and internal hardware; the PVC panels act as the skin.

### 3. Cut the PVC panels

From the 1/4″ Veranda PVC sheet(s):

1. Cut **front and back panels** to 22″ × 62″ with the roof peak profile.
2. Cut **side panels**:
   - 8″ deep, with a 31″ lower vertical section and a 23″ upper angled section to match the roof pitch.
3. Cut the **bottom panel** to 22″ × 8″.
4. Cut two **roof panels** at 24″ × 10″ for an overhanging roof on both sides.

Test fit each panel against the 2×4 frame and trim as needed.

### 4. Cut and mount the monitor window

1. On the **front panel**, mark the opening for the 24″ monitor:
   - Centered horizontally on the 22″ width.
   - Vertical placement based on your monitor height and desired sight line.
2. Cut the opening slightly smaller than the visible area of the monitor to create a bezel.
3. Cut a piece of plexiglass (~22″ × 14″) to cover the opening.
4. Mount the plexiglass over the opening using:
   - Screws with pre-drilled holes, or
   - PVC trim as a picture frame around the edges.
5. Seal the edges with exterior sealant if needed to keep water out.

### 5. Attach PVC panels to the frame

1. Pre-drill holes in the PVC so it does not crack when screwing into the 2×4 frame.
2. Attach the **front panel** to the internal frame with #8 × 1-1/4″ screws.
3. Attach the **back panel** in the same way.
4. Attach the **side panels** to both the frame and the edges of the front/back panels.
5. Install the **bottom panel** onto the 2×4 bottom cleats.

At this stage you should have a rigid birdhouse-style shell with a framed monitor window in the front.

### 6. Create and install the service door

1. Decide whether the **back** or one **side** will serve as the service door.
2. Mark and cut a door opening in that panel, leaving a perimeter frame for strength.
3. Attach the cut-out piece back to the frame with two exterior hinges to form a swing door.
4. Add a latch or magnetic catch to keep the door closed.
5. Add weatherstrip along the door edges to reduce water and dust ingress.

### 7. Mount roof panels and seal everything

1. Attach the two **roof panels** to the ridge and upper frame:
   - Allow a small overhang front and back for drip protection.
2. Screw through the PVC roof panels into the ridge cleat and top edges of the side walls.
3. Seal all seams (roof-to-wall, panel joints, door frame edges) with exterior-grade sealant.
4. Check for any gaps where water might get in and seal as needed.

### 8. Add cable glands and prepare for wiring

1. Drill holes in the **bottom** or lower **back** panel for cable glands:
   - Power into the box
   - Radar sensors
   - Letter sensor
   - Optional HDMI or other cables
2. Install cable glands and snug them down.
3. Do not route cables yet; that will happen after the electronics are mounted.

### 9. Mount internal electronics

1. Attach a small board or plate inside (if desired) to mount:
   - Raspberry Pi
   - Power supply / power brick
   - Any additional modules
2. Mount the 24″ monitor to the monitor brace or internal bracket:
   - Use the VESA mount if available, or a simple custom bracket screwed to the brace.
3. Plan cable paths so HDMI, power, and sensor wires are protected and tidy.
4. Use zip ties and anchors for strain relief and cable management.

### 10. Install sensors and final assembly

1. Install the **letter sensor** (IR breakbeam or switch) inside the letter slot or window area:
   - Ensure every dropped letter reliably triggers the sensor.
2. Mount the **radar sensors** externally (or in a companion housing) aimed at the traffic area:
   - Respect the manufacturer’s angle recommendations.
   - Route their cables into the box through the glands.
3. Route all cables through the glands and secure them inside.
4. Close the service door and verify everything opens and closes cleanly.

Once the enclosure is built and wired, follow the software installation and configuration steps below to bring SLED online.

## Hardware

Minimum:

- Raspberry Pi 3B or newer  
- HDMI display or TV (24″ in the reference build)  
- Letter sensor (IR breakbeam or mechanical switch)  
- Two radar sensors with digital outputs (for direction A and B)  
- 5 V power for the Pi and sensors  

Suggested defaults (matching `config.yaml.example`):

| Function       | BCM Pin | Notes                     |
| ------------- | ------- | ------------------------- |
| Letter sensor | 17      | Active low or high input  |
| Radar A       | 27      | First radar beam          |
| Radar B       | 22      | Second radar beam         |

Adjust pins in `config.yaml` if you wire differently.

## Software stack

- Python 3  
- mpv for video playback  
- paho-mqtt for MQTT  
- PyYAML for configuration  
- Home Assistant with MQTT integration  

## Installation

On Raspberry Pi OS:

```bash
sudo apt update
sudo apt install -y python3 python3-pip mpv
