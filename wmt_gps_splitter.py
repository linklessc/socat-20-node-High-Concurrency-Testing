#!/usr/bin/env python3
import os
import pty
import time
import signal
import sys
import fcntl
import glob
import re
import termios
import struct
import tty  # Imported to set terminal to Raw Mode (prevents auto-formatting like \n -> \r\n)

# === Configuration ===
CONFIG_FILE = "/bin/wmt_winset/config.ini"
SOURCE_DEV = "/dev/ttyACM0"   # Path to the physical GPS device
START_ID = 0

# [Anti-Lag Threshold]
# Set to 4096 bytes (approx. 3-4 seconds of GPS data volume).
# If the accumulated buffer (Kernel + Python) exceeds this value, 
# the buffer is flushed to ensure the data remains real-time.
MAX_BUFFER_BACKLOG = 4096

# Global variable to track active virtual ports for cleanup on exit
active_virtual_ports = []

def load_config():
    """
    Reads the configuration file to determine how many virtual GPS ports to open.
    Returns:
        int: The end ID (number of ports), or -1 if disabled/error.
    """
    end_id = -1
    if not os.path.exists(CONFIG_FILE):
        return -1
    try:
        with open(CONFIG_FILE, 'r') as f:
            for line in f:
                # Look for the 'gps_port' setting
                if line.strip().startswith("gps_port"):
                    parts = line.split('=')
                    if len(parts) > 1:
                        val = parts[1].strip().lower()
                        if val == 'false':
                            return -1
                        try:
                            end_id = int(val)
                            return end_id
                        except ValueError:
                            return -1
    except Exception:
        pass
    return end_id

def set_non_blocking(fd):
    """
    Sets a File Descriptor (FD) to non-blocking mode.
    This prevents the script from freezing if a write operation cannot complete immediately.
    """
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

def force_cleanup_at_startup():
    """
    Force Startup Cleanup:
    Scans for any existing /dev/gps* files (symlinks or nodes) and deletes them 
    to ensure a clean state before starting.
    """
    print("Performing startup cleanup...")
    file_list = glob.glob("/dev/gps*")
    for file_path in file_list:
        # Strict regex to ensure we only delete files named like /dev/gps0, /dev/gps1, etc.
        if re.match(r"^/dev/gps\d+$", file_path):
            try:
                if os.path.islink(file_path) or os.path.exists(file_path):
                    os.unlink(file_path)
                    print(f" -> Removed stale file: {file_path}")
            except OSError as e:
                print(f"Error removing {file_path}: {e}")

def cleanup_symlinks(virtual_ports):
    """
    Removes the symlinks created during the current execution session.
    Called on script exit.
    """
    print("Cleaning up active symlinks...")
    for _, link_name in virtual_ports:
        if os.path.exists(link_name):
            try:
                os.unlink(link_name)
                print(f"Removed {link_name}")
            except OSError as e:
                print(f"Error removing {link_name}: {e}")

def handle_sigterm(signum, frame):
    """
    Signal Handler for SIGTERM/SIGINT (Ctrl+C).
    Ensures a graceful exit by cleaning up symlinks.
    """
    print(f"Received signal {signum}, exiting gracefully...")
    cleanup_symlinks(active_virtual_ports)
    sys.exit(0)

def main():
    # Register signal listeners for graceful shutdown
    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT, handle_sigterm)

    # 1. Perform initial cleanup of stale GPS files
    force_cleanup_at_startup()

    # Load configuration to decide how many ports to create
    end_id = load_config()
    if end_id <= 0:
        print("GPS Splitter disabled via config. Exiting.")
        sys.exit(0)

    print(f"=== Starting Python GPS Splitter (0 to {end_id}) ===")
    print(f"=== Anti-Lag Mode: Enabled (Threshold: {MAX_BUFFER_BACKLOG} bytes) ===")
    
    # 2. Wait for and open the physical GPS source device
    retry_count = 0
    while not os.path.exists(SOURCE_DEV):
        print(f"Waiting for {SOURCE_DEV}...")
        time.sleep(2)
        retry_count += 1
        if retry_count > 30:
            print(f"Device {SOURCE_DEV} not found after timeout.")
            sys.exit(1)
        
    try:
        # Open source in Read-Only mode, NOCTTY prevents it from becoming the controlling terminal
        source_fd = os.open(SOURCE_DEV, os.O_RDONLY | os.O_NOCTTY)
    except OSError as e:
        print(f"Failed to open source {SOURCE_DEV}: {e}")
        sys.exit(1)

    virtual_fds = []
    
    # Dictionary to store pending data for each port.
    # Format: { fd: b"pending data..." }
    # This acts as a compensation buffer for partial writes.
    port_buffers = {} 

    # 3. Create virtual Ports (PTY pairs)
    for i in range(START_ID, end_id):
        try:
            # Create a pseudo-terminal pair (master side used by script, slave side used by apps)
            master_fd, slave_fd = pty.openpty()
            
            # === CRITICAL: Set Raw Mode ===
            # This disables all automatic terminal processing (echo, CR/LF conversion, etc.)
            # ensuring byte-perfect data transfer.
            tty.setraw(master_fd)
            tty.setraw(slave_fd)

            # Set Master FD to non-blocking to prevent the script from hanging on write
            set_non_blocking(master_fd)
            
            slave_name = os.ttyname(slave_fd)
            link_name = f"/dev/gps{i}"

            # Remove existing link if it exists
            if os.path.exists(link_name):
                try:
                    os.unlink(link_name)
                except OSError:
                    pass

            # Create symlink for the application to use (e.g., /dev/gps0 -> /dev/pts/X)
            os.symlink(slave_name, link_name)
            os.chmod(link_name, 0o666)
            os.chmod(slave_name, 0o666)

            virtual_fds.append(master_fd)
            active_virtual_ports.append((master_fd, link_name))
            
            # Initialize the write buffer for this port
            port_buffers[master_fd] = b""

            print(f" -> Created {link_name} connected to {slave_name} (Raw Mode)")
        except Exception as e:
            print(f"Error creating port {i}: {e}")

    print("=== Splitting Service Running... ===")

    # Buffer to hold incomplete NMEA sentences from the source
    leftover = b""

    # 4. Main Data Loop
    try:
        while True:
            try:
                # Read raw data from physical GPS (up to 4096 bytes)
                raw_data = os.read(source_fd, 4096)
                if not raw_data:
                    print("Source EOF")
                    break
                
                # === Data Processing (Stitching Logic) ===
                # Combine leftover data from previous loop with new data
                data_to_process = leftover + raw_data
                
                # Find the last newline character. NMEA sentences always end in \n.
                last_newline = data_to_process.rfind(b'\n')

                if last_newline != -1:
                    # Extract valid, complete sentences up to the newline
                    final_payload = data_to_process[:last_newline+1]
                    # Save the rest (incomplete fragment) for the next loop
                    leftover = data_to_process[last_newline+1:]
                else:
                    # No newline found, keep everything in leftover and wait for more data
                    leftover = data_to_process
                    continue 

                # === Write to all virtual ports (Optimized Logic) ===
                for fd in virtual_fds:
                    try:
                        # 1. Append new data to the port's specific buffer
                        port_buffers[fd] += final_payload

                        # 2. Try to write the buffer content to the virtual port
                        try:
                            if port_buffers[fd]:
                                # os.write returns the number of bytes actually written
                                written = os.write(fd, port_buffers[fd])
                                # Slice the buffer to keep only what wasn't written
                                port_buffers[fd] = port_buffers[fd][written:]
                        except BlockingIOError:
                            # Buffer is full, os.write couldn't write anything.
                            # Data remains in port_buffers[fd] to be retried next loop.
                            pass

                        # 3. [Anti-Lag Logic] Check total backlog size
                        # Calculate pending bytes in Kernel buffer (TIOCOUTQ)
                        buf_info = fcntl.ioctl(fd, termios.TIOCOUTQ, struct.pack('I', 0))
                        pending_kernel = struct.unpack('I', buf_info)[0]
                        
                        # Calculate pending bytes in Python buffer
                        pending_python = len(port_buffers[fd])

                        # If total backlog exceeds threshold, force flush to maintain real-time data
                        if (pending_kernel + pending_python) > MAX_BUFFER_BACKLOG:
                            termios.tcflush(fd, termios.TCOFLUSH) # Flush Kernel buffer
                            port_buffers[fd] = b""               # Clear Python buffer
                            # Note: Data is lost here to prevent massive lag.

                    except OSError:
                        # Port might be disconnected or in a bad state
                        pass

            except OSError:
                break
    except Exception as e:
        print(f"Loop error: {e}")
    finally:
        cleanup_symlinks(active_virtual_ports)
        try:
            os.close(source_fd)
        except:
            pass

if __name__ == "__main__":
    main()
