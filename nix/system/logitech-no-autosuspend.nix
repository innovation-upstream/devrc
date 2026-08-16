# Disable USB autosuspend for Logitech wireless receivers
# Prevents mouse from sleeping after short idle periods (2s default)
{ ... }: {
  services.udev.extraRules = ''
    # Disable autosuspend for all Logitech USB receivers
    ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="046d", TEST=="power/control", ATTR{power/control}="on"
    ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="046d", TEST=="power/autosuspend", ATTR{power/autosuspend}="-1"
  '';
}
