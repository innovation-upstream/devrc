# iPhone connectivity: usbmuxd daemon + libimobiledevice tools.
# Applied by nix/system/apply-iphone-support.sh (system-level, so the agent stages
# this and the operator runs the script under sudo — never a direct nixos-rebuild).
# Verify after the switch with a device plugged in:
#   idevice_id -l          (device UDID once paired)
#   ideviceinfo            (full device info)
#   idevicepair pair       (trust handshake; accept the "Trust" prompt on the phone)
#   ifuse ~/mnt/iphone     (FUSE mount of the device filesystem)
{ pkgs, ... }: {
  services.usbmuxd.enable = true;

  environment.systemPackages = with pkgs; [
    libimobiledevice # idevice_id, ideviceinfo, idevicepair, idevicebackup2, ...
    ifuse # mount the device filesystem via FUSE
  ];
}
