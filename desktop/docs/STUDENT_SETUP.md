# Installing UAI Aegis (for students)

Install the app before the exam day and sign in once to check it works. Download it only
from the college website link your professor gives you.

## Windows 10 / 11

1. Run `UAI Aegis_x.y.z_x64-setup.exe`. If Windows shows a blue SmartScreen box, check that
   the publisher says **Universal AI University**, then choose More info > Run anyway.
2. Open UAI Aegis and sign in with your college email.
3. The app opens on its own exam screen. Use its **Quit** button to get back to Windows.

## macOS

1. Open the `.dmg` and drag UAI Aegis to Applications. Open it from Applications.
2. Sign in. When you press Start the first time, macOS asks to allow UAI Aegis under
   **System Settings > Privacy & Security > Accessibility**: switch it on, quit the app
   and open it again.
3. During the exam the Dock, menu bar and app switching are off. Quit the app from its
   own Quit button after the exam.

## Linux (Ubuntu / Debian, Fedora)

1. Install the package (it also installs `cage`, the small program that runs the exam
   screen):
   - Ubuntu / Debian: `sudo apt install ./uai-aegis_x.y.z_amd64.deb`
   - Fedora: `sudo dnf install ./uai-aegis-x.y.z-1.x86_64.rpm`
2. Log out. On the login screen, click your name, then the session (gear) button, choose
   **UAI Aegis Exam** and sign in. Only the exam app opens.
3. Quitting the app logs you out; next time pick your normal session again.

## Before every exam

- Charge the laptop and connect to the college Wi-Fi.
- Close Discord, Teams, Zoom, AnyDesk, TeamViewer, WhatsApp, Telegram, AI assistant apps
  and screen recorders (check the system tray / menu bar too). Unplug extra monitors.
- Set the date and time to automatic.
- Do not use a virtual machine.

The app checks all of this when you press Start and tells you what to fix. During the exam,
the app brings you straight back if you leave the exam screen (in a fraction of a second),
and each time you leave counts as one strike; too many strikes, or something serious like
remote-control software, pauses your exam until your professor lets you continue.

## If your laptop stops working during the exam

1. Tell your professor or the invigilator.
2. On another laptop (a spare one, or a friend's) with UAI Aegis installed, sign in with
   **your own** account and open the same exam. It stays paused until your professor
   approves the new laptop.
3. Your code is there: it is saved on the server every few seconds. The professor can give
   you back the minutes you lost.

## Data files in a question

Some questions come with data files (for example `marks.csv`). They are listed under the
question: click one to see its first rows and a summary of its columns. Your code opens
the file by its name, e.g. `pd.read_csv("marks.csv")`; it is next to your program when it
runs. You don't download anything.
