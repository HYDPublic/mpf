import logging
import time
from mpf.core.platform import MatrixLightsPlatform, GiPlatform, LedPlatform, SwitchPlatform, DriverPlatform
from mpf.core.rgb_color import RGBColor
from mpf.core.utility_functions import Util
from mpf.platforms.interfaces.driver_platform_interface import DriverPlatformInterface
from mpf.platforms.interfaces.matrix_light_platform_interface import MatrixLightPlatformInterface
from mpf.platforms.interfaces.rgb_led_platform_interface import RGBLEDPlatformInterface

try:
    import pinproc
    pinproc_imported = True
except ImportError:
    pinproc_imported = False
    pinproc = None


class PROCBasePlatform(MatrixLightsPlatform, GiPlatform, LedPlatform, SwitchPlatform, DriverPlatform):
    """Platform class for the P-Roc and P3-ROC hardware controller.

    Args:
        machine: The MachineController instance.

    Attributes:
        proc: The pinproc.PinPROC device.
        machine_type: Constant of the pinproc.MachineType
    """

    def __init__(self, machine):
        super().__init__(machine)

        if not pinproc_imported:
            raise AssertionError('Could not import "pinproc". Most likely you do not '
                                 'have libpinproc and/or pypinproc installed. You can '
                                 'run MPF in software-only "virtual" mode by using '
                                 'the -x command like option for now instead.')

        self.pinproc = pinproc
        self.proc = None
        self.log = None
        self.hw_switch_rules = {}

        self.machine_type = pinproc.normalize_machine_type(
            self.machine.config['hardware']['driverboards'])

    def connect(self):
        # Connect to the P-ROC. Keep trying if it doesn't work the first time.
        self.log.info("Connecting to P-ROC")

        while not self.proc:
            try:
                self.proc = pinproc.PinPROC(self.machine_type)
                self.proc.reset(1)
            except IOError:
                print("Retrying...")
                time.sleep(1)

        self.log.info("Successfully connected to P-ROC/P3-ROC")

    def _get_event_type(self, sw_activity, debounced):
        if sw_activity == 0 and debounced:
            return "open_debounced"
        elif sw_activity == 0 and not debounced:
            return "open_nondebounced"
        elif sw_activity == 1 and debounced:
            return "closed_debounced"
        else:  # if sw_activity == 1 and not debounced:
            return "closed_nondebounced"

    def get_coil_config_section(self):
        return "p_roc_coils"

    def get_switch_overwrite_section(self):
        return "p_roc_switch_overwrites"

    def get_coil_overwrite_section(self):
        return "p_roc_coil_overwrites"

    def add_pulse_rule_to_switch(self, switch, coil):
        # TODO: properly implement pulse_power. previously implemented pwm_on_ms/pwm_off_ms were incorrect here

        switch.hw_switch.hw_rules[self._get_event_type(not switch.invert, switch.config['debounce'])].append(
            (switch.hw_switch.number, coil.hw_driver.number,
             self.pinproc.driver_state_pulse(coil.hw_driver.state(), coil.hw_driver.get_pulse_ms(coil)))
        )

    def add_pulse_and_hold_rule_to_switch(self, switch, coil):
        if coil.hw_driver.get_pwm_on_ms(coil) and coil.hw_driver.get_pwm_off_ms(coil):
            switch.hw_switch.hw_rules[self._get_event_type(not switch.invert, switch.config['debounce'])].append(
                (switch.hw_switch.number, coil.hw_driver.number,
                 self.pinproc.driver_state_patter(
                    coil.hw_driver.state(), coil.hw_driver.get_pwm_on_ms(coil), coil.hw_driver.get_pwm_off_ms(coil),
                    coil.hw_driver.get_pulse_ms(coil), True))
            )
        else:
            if not coil.config['allow_enable']:
                raise AssertionError("Coil {} may not be enabled at 100% without allow_enabled or pwm settings".format(
                    coil.hw_driver.number
                ))
            switch.hw_switch.hw_rules[self._get_event_type(not switch.invert, switch.config['debounce'])].append(
                (switch.hw_switch.number, coil.hw_driver.number, self.pinproc.driver_state_pulse(coil.hw_driver.state(), 0))
            )

    def add_relase_disable_rule_to_switch(self, switch, coil):
        switch.hw_switch.hw_rules[self._get_event_type(switch.invert, switch.config['debounce'])].append(
            (switch.hw_switch.number, coil.hw_driver.number, self.pinproc.driver_state_disable(coil.hw_driver.state()))
        )

    def add_disable_rule_to_switch(self, switch, coil):
        switch.hw_switch.hw_rules[self._get_event_type(not switch.invert, switch.config['debounce'])].append(
            (switch.hw_switch.number, coil.hw_driver.number,
             self.pinproc.driver_state_disable(coil.hw_driver.state()))
        )

    def write_rules_to_switch(self, switch, coil, drive_now):
        for event_type, driver_rules in switch.hw_switch.hw_rules.items():
            driver = []
            for x in driver_rules:
                driver.append(x[2])
            rule = {'notifyHost': bool(switch.hw_switch.notify_on_debounce) == event_type.endswith("nondebounced"),
                    'reloadActive': bool(coil.config['recycle'])}
            if drive_now is None:
                self.proc.switch_update_rule(switch.hw_switch.number, event_type, rule, driver)
            else:
                self.proc.switch_update_rule(switch.hw_switch.number, event_type, rule, driver, drive_now)

    def set_pulse_on_hit_rule(self, enable_switch, coil):
        self.log.debug("Setting HW Rule on pulse on hit. Switch: %s, Driver: %s",
                       enable_switch.hw_switch.number, coil.hw_driver.number)

        self.add_pulse_rule_to_switch(enable_switch, coil)

        self.write_rules_to_switch(enable_switch, coil, False)

    def set_pulse_on_hit_and_release_rule(self, enable_switch, coil):
        self.log.debug("Setting HW Rule on pulse on hit and relesae. Switch: %s, Driver: %s",
                       enable_switch.hw_switch.number, coil.hw_driver.number)

        self.add_pulse_rule_to_switch(enable_switch, coil)
        self.add_relase_disable_rule_to_switch(enable_switch, coil)

        self.write_rules_to_switch(enable_switch, coil, False)

    def set_pulse_on_hit_and_enable_and_release_rule(self, enable_switch, coil):
        self.log.debug("Setting Pulse on hit and enable and release HW Rule. Switch: %s, Driver: %s",
                       enable_switch.hw_switch.number, coil.hw_driver.number)

        self.add_pulse_and_hold_rule_to_switch(enable_switch, coil)
        self.add_relase_disable_rule_to_switch(enable_switch, coil)

        self.write_rules_to_switch(enable_switch, coil, False)

    def set_pulse_on_hit_and_enable_and_release_and_disable_rule(self, enable_switch, disable_switch, coil):
        self.log.debug("Setting Pulse on hit and enable and release and disable HW Rule. Enable Switch: %s,"
                       "Disable Switch: %s, Driver: %s", enable_switch.hw_switch.number,
                       disable_switch.hw_switch.number, coil.hw_driver.number)

        self.add_pulse_and_hold_rule_to_switch(enable_switch, coil)
        self.add_relase_disable_rule_to_switch(enable_switch, coil)
        self.add_disable_rule_to_switch(disable_switch, coil)

        self.write_rules_to_switch(enable_switch, coil, False)
        self.write_rules_to_switch(disable_switch, coil, False)

    def clear_hw_rule(self, switch, coil):
        """Clears a hardware rule.

        This is used if you want to remove the linkage between a switch and
        some driver activity. For example, if you wanted to disable your
        flippers (so that a player pushing the flipper buttons wouldn't cause
        the flippers to flip), you'd call this method with your flipper button
        as the *sw_num*.

        Args:
            switch: Switch object
            coil: Coil object
        """

        self.log.debug("Clearing HW rule for switch: %s coil: %s", switch.hw_switch.number, coil.hw_driver.number)

        coil_number = False
        for entry, element in switch.hw_switch.hw_rules.items():
            if not element:
                continue
            for rule_num, rule in enumerate(element):
                if rule[0] == switch.hw_switch.number and rule[1] == coil.hw_driver.number:
                    coil_number = rule[2]['driverNum']
                    del switch.hw_switch.hw_rules[entry][rule_num]

        if coil_number:
            self.proc.driver_disable(coil_number)
            self.write_rules_to_switch(switch, coil, None)

        return bool(coil_number)

    def configure_gi(self, config):
        """Configures a P-ROC GI string light."""
        # On the P/P3-ROC, GI strings are drivers
        return self.configure_driver(config)

    def configure_led(self, config):
        """ Configures a P/P3-ROC RGB LED controlled via a PD-LED."""

        # split the number (which comes in as a string like w-x-y-z) into parts
        config['number'] = str(config['number']).split('-')

        if 'polarity' in config:
            invert = not config['polarity']
        else:
            invert = False

        return PDBLED(board=int(config['number'][0]),
                      address=[int(config['number'][1]),
                               int(config['number'][2]),
                               int(config['number'][3])],
                      proc_driver=self.proc,
                      invert=invert)

    def _configure_switch(self, config, proc_num):
        """Configures a P3-ROC switch.

        Args:
            config: Dictionary of settings for the switch.
            proc_num: decoded switch number

        Returns:
            switch : A reference to the switch object that was just created.
            proc_num : Integer of the actual hardware switch number the P3-ROC
                uses to refer to this switch. Typically your machine
                configuration files would specify a switch number like `SD12` or
                `7/5`. This `proc_num` is an int between 0 and 255.
        """
        if proc_num == -1:
            raise AssertionError("Switch %s cannot be controlled by the "
                                 "P-ROC/P3-ROC.", str(config['number']))

        switch = PROCSwitch(config, proc_num, not config['debounce'])
        # The P3-ROC needs to be configured to notify the host computers of
        # switch events. (That notification can be for open or closed,
        # debounced or nondebounced.)
        self.log.debug("Configuring switch's host notification settings. P3-ROC"
                       "number: %s, debounce: %s", proc_num,
                       config['debounce'])
        if config['debounce'] is False:
            self.proc.switch_update_rule(proc_num, 'closed_nondebounced',
                                         {'notifyHost': True,
                                          'reloadActive': False}, [], False)
            self.proc.switch_update_rule(proc_num, 'open_nondebounced',
                                         {'notifyHost': True,
                                          'reloadActive': False}, [], False)
        else:
            self.proc.switch_update_rule(proc_num, 'closed_debounced',
                                         {'notifyHost': True,
                                          'reloadActive': False}, [], False)
            self.proc.switch_update_rule(proc_num, 'open_debounced',
                                         {'notifyHost': True,
                                          'reloadActive': False}, [], False)
        return switch, proc_num


class PDBConfig(object):
    """ This class is only used when using the P3-ROC or when the P-ROC is configured to use PDB
    driver boards such as the PD-16 or PD-8x8. i.e. not when it's operating in
    WPC or Stern mode.

    """
    indexes = []
    proc = None

    def __init__(self, proc, config, driver_count):

        self.log = logging.getLogger('PDBConfig')
        self.log.debug("Processing PDB Driver Board configuration")

        self.proc = proc

        # Set config defaults
        self.lamp_matrix_strobe_time = config['p_roc']['lamp_matrix_strobe_time']
        self.watchdog_time = config['p_roc']['watchdog_time']
        self.use_watchdog = config['p_roc']['use_watchdog']

        # Initialize some lists for data collecting
        coil_bank_list = self._load_coil_bank_list_from_config(config)
        lamp_source_bank_list, lamp_list, lamp_list_for_index = self._load_lamp_lists_from_config(config)

        # Create a list of indexes.  The PDB banks will be mapped into this
        # list. The index of the bank is used to calculate the P-ROC/P3-ROC driver
        # number for each driver.
        num_proc_banks = driver_count // 8
        self.indexes = [99] * num_proc_banks

        self.initialize_drivers(proc)

        # Set up dedicated driver groups (groups 0-3).
        for group_ctr in range(0, 4):
            # TODO: Fix this.  PDB Banks 0-3 are also interpreted as dedicated
            # bank here.
            enable = group_ctr in coil_bank_list
            self.log.debug("Driver group %02d (dedicated): Enable=%s",
                           group_ctr, enable)
            proc.driver_update_group_config(group_ctr,
                                            0,
                                            group_ctr,
                                            0,
                                            0,
                                            False,
                                            True,
                                            enable,
                                            True)

        # next group is 4
        group_ctr = 4

        # Process lamps first. The P-ROC/P3-ROC can only control so many drivers
        # directly. Since software won't have the speed to control lamp
        # matrixes, map the lamps first. If there aren't enough driver
        # groups for coils, the overflow coils can be controlled by software
        # via VirtualDrivers (which should get set up automatically by this
        # code.)

        for i, lamp_dict in enumerate(lamp_list):
            # If the bank is 16 or higher, the P-ROC/P3-ROC can't control it
            # directly. Software can't really control lamp matrixes either
            # (need microsecond resolution).  Instead of doing crazy logic here
            # for a case that probably won't happen, just ignore these banks.
            if group_ctr >= num_proc_banks or lamp_dict['sink_bank'] >= 16:
                raise AssertionError("Lamp matrix banks can't be mapped to index "
                                     "%d because that's outside of the banks the "
                                     "P-ROC/P3-ROC can control.", lamp_dict['sink_bank'])
            else:
                self.log.debug("Driver group %02d (lamp sink): slow_time=%d "
                               "enable_index=%d row_activate_index=%d "
                               "row_enable_index=%d matrix=%s", group_ctr,
                               self.lamp_matrix_strobe_time,
                               lamp_dict['sink_bank'],
                               lamp_dict['source_output'],
                               lamp_dict['source_index'], True)
                self.indexes[group_ctr] = lamp_list_for_index[i]
                proc.driver_update_group_config(group_ctr,
                                                self.lamp_matrix_strobe_time,
                                                lamp_dict['sink_bank'],
                                                lamp_dict['source_output'],
                                                lamp_dict['source_index'],
                                                True,
                                                True,
                                                True,
                                                True)
                group_ctr += 1

        for coil_bank in coil_bank_list:
            # If the bank is 16 or higher, the P-ROC/P3-ROC can't control it directly.
            # Software will have do the driver logic and write any changes to
            # the PDB bus. Therefore, map these banks to indexes above the
            # driver count, which will force the drivers to be created
            # as VirtualDrivers. Appending the bank avoids conflicts when
            # group_ctr gets too high.

            if group_ctr >= num_proc_banks or coil_bank >= 32:
                self.log.warning("Driver group %d mapped to driver index"
                                 "outside of P-ROC/P3-ROC control.  These Drivers "
                                 "will become VirtualDrivers.  Note, the "
                                 "index will not match the board/bank "
                                 "number; so software will need to request "
                                 "those values before updating the "
                                 "drivers.", coil_bank)
                self.indexes.append(coil_bank)
            else:
                self.log.debug("Driver group %02d: slow_time=%d Enable "
                               "Index=%d", group_ctr, 0, coil_bank)
                self.indexes[group_ctr] = coil_bank
                proc.driver_update_group_config(group_ctr,
                                                0,
                                                coil_bank,
                                                0,
                                                0,
                                                False,
                                                True,
                                                True,
                                                True)
                group_ctr += 1

        for i in range(group_ctr, 26):
            self.log.debug("Driver group %02d: disabled", i)
            proc.driver_update_group_config(i,
                                            self.lamp_matrix_strobe_time,
                                            0,
                                            0,
                                            0,
                                            False,
                                            True,
                                            False,
                                            True)

        # Make sure there are two indexes.  If not, fill them in.
        while len(lamp_source_bank_list) < 2:
            lamp_source_bank_list.append(0)

        # Now set up globals.  First disable them to allow the P-ROC/P3-ROC to set up
        # the polarities on the Drivers.  Then enable them.
        self.configure_globals(proc, lamp_source_bank_list, False)
        self.configure_globals(proc, lamp_source_bank_list, True)

    def _load_lamp_lists_from_config(self, config):
        lamp_source_bank_list = []
        lamp_list = []
        lamp_list_for_index = []

        # Make a list of unique lamp source banks.  The P-ROC/P3-ROC only supports 2.
        # TODO: What should be done if 2 is exceeded?
        if 'matrix_lights' in config:
            for name in config['matrix_lights']:
                item_dict = config['matrix_lights'][name]
                lamp = PDBLight(self, str(item_dict['number']))

                # Catalog PDB banks
                # Dedicated lamps don't use PDB banks. They use P-ROC direct
                # driver pins (not available on P3-ROC).
                if lamp.lamp_type == 'dedicated':
                    pass

                elif lamp.lamp_type == 'pdb':
                    if lamp.source_bank() not in lamp_source_bank_list:
                        lamp_source_bank_list.append(lamp.source_bank())

                    # Create dicts of unique sink banks.  The source index is
                    # needed when setting up the driver groups.
                    lamp_dict = {'source_index':
                                 lamp_source_bank_list.index(lamp.source_bank()),
                                 'sink_bank': lamp.sink_bank(),
                                 'source_output': lamp.source_output()}

                    # lamp_dict_for_index.  This will be used later when the
                    # p-roc numbers are requested.  The requestor won't know
                    # the source_index, but it will know the source board.
                    # This is why two separate lists are needed.
                    lamp_dict_for_index = {'source_board': lamp.source_board(),
                                           'sink_bank': lamp.sink_bank(),
                                           'source_output':
                                               lamp.source_output()}

                    if lamp_dict not in lamp_list:
                        lamp_list.append(lamp_dict)
                        lamp_list_for_index.append(lamp_dict_for_index)

        return lamp_source_bank_list, lamp_list, lamp_list_for_index

    def _load_coil_bank_list_from_config(self, config):
        coil_bank_list = []

        # Make a list of unique coil banks
        if 'coils' in config:
            for name in config['coils']:
                item_dict = config['coils'][name]
                coil = PDBCoil(self, str(item_dict['number']))
                if coil.bank() not in coil_bank_list:
                    coil_bank_list.append(coil.bank())

        return coil_bank_list

    def initialize_drivers(self, proc):
        # Loop through all of the drivers, initializing them with the polarity.
        for i in range(0, 255):
            state = {'driverNum': i,
                     'outputDriveTime': 0,
                     'polarity': True,
                     'state': False,
                     'waitForFirstTimeSlot': False,
                     'timeslots': 0,
                     'patterOnTime': 0,
                     'patterOffTime': 0,
                     'patterEnable': False,
                     'futureEnable': False}

            proc.driver_update_state(state)

    def configure_globals(self, proc, lamp_source_bank_list, enable=True):

        if enable:
            self.log.debug("Configuring PDB Driver Globals:  polarity = %s  "
                           "matrix column index 0 = %d  matrix column index "
                           "1 = %d", True, lamp_source_bank_list[0],
                           lamp_source_bank_list[1])
        proc.driver_update_global_config(enable,  # Don't enable outputs yet
                                         True,  # Polarity
                                         False,  # N/A
                                         False,  # N/A
                                         1,  # N/A
                                         lamp_source_bank_list[0],
                                         lamp_source_bank_list[1],
                                         False,  # Active low rows? No
                                         False,  # N/A
                                         False,  # Stern? No
                                         False,  # Reset watchdog trigger
                                         self.use_watchdog,  # Enable watchdog
                                         self.watchdog_time)

        # Now set up globals
        proc.driver_update_global_config(True,  # Don't enable outputs yet
                                         True,  # Polarity
                                         False,  # N/A
                                         False,  # N/A
                                         1,  # N/A
                                         lamp_source_bank_list[0],
                                         lamp_source_bank_list[1],
                                         False,  # Active low rows? No
                                         False,  # N/A
                                         False,  # Stern? No
                                         False,  # Reset watchdog trigger
                                         self.use_watchdog,  # Enable watchdog
                                         self.watchdog_time)

    def get_proc_number(self, device_type, number_str):
        """Returns the P-ROC/P3-ROC number for the requested driver string.

        This method uses the driver string to look in the indexes list that
        was set up when the PDBs were configured.  The resulting P-ROC/P3-ROC index
        * 3 is the first driver number in the group, and the driver offset is
        to that.

        """
        if device_type == 'coil':
            coil = PDBCoil(self, number_str)
            bank = coil.bank()
            if bank == -1:
                return -1
            index = self.indexes.index(coil.bank())
            num = index * 8 + coil.output()
            return num

        if device_type == 'light':
            lamp = PDBLight(self, number_str)
            if lamp.lamp_type == 'unknown':
                return -1
            elif lamp.lamp_type == 'dedicated':
                return lamp.dedicated_output()

            lamp_dict_for_index = {'source_board': lamp.source_board(),
                                   'sink_bank': lamp.sink_bank(),
                                   'source_output': lamp.source_output()}
            if lamp_dict_for_index not in self.indexes:
                return -1
            index = self.indexes.index(lamp_dict_for_index)
            num = index * 8 + lamp.sink_output()
            return num

        if device_type == 'switch':
            switch = PDBSwitch(self, number_str)
            num = switch.proc_num()
            return num


class PDBSwitch(object):
    """Base class for switches connected to a P-ROC/P3-ROC."""

    def __init__(self, pdb, number_str):
        del pdb  # unused. why?

        upper_str = number_str.upper()
        if upper_str.startswith('SD'):  # only P-ROC
            self.sw_type = 'dedicated'
            self.sw_number = int(upper_str[2:])
        elif '/' in upper_str:  # only P-ROC
            self.sw_type = 'matrix'
            self.sw_number = self.parse_matrix_num(upper_str)
        else:   # only P3-Roc
            self.sw_type = 'proc'
            try:
                (boardnum, banknum, inputnum) = decode_pdb_address(number_str)
                self.sw_number = boardnum * 16 + banknum * 8 + inputnum
            except ValueError:
                try:
                    self.sw_number = int(number_str)
                except:
                    raise ValueError('Switch %s is invalid. Use either PDB '
                                     'format or an int', str(number_str))

    def proc_num(self):
        return self.sw_number

    def parse_matrix_num(self, num_str):
        cr_list = num_str.split('/')
        return 32 + int(cr_list[0]) * 16 + int(cr_list[1])


class PDBCoil(object):
    """Base class for coils connected to a P-ROC/P3-ROC that are controlled via PDB
    driver boards (i.e. the PD-16 board).

    """

    def __init__(self, pdb, number_str):
        self.pdb = pdb
        upper_str = number_str.upper()
        if self.is_direct_coil(upper_str):
            self.coil_type = 'dedicated'
            self.banknum = (int(number_str[1:]) - 1) / 8
            self.outputnum = int(number_str[1:])
        elif self.is_pdb_coil(number_str):
            self.coil_type = 'pdb'
            (self.boardnum, self.banknum, self.outputnum) = decode_pdb_address(number_str)
        else:
            self.coil_type = 'unknown'

    def bank(self):
        if self.coil_type == 'dedicated':
            return self.banknum
        elif self.coil_type == 'pdb':
            return self.boardnum * 2 + self.banknum
        else:
            return -1

    def output(self):
        return self.outputnum

    def is_direct_coil(self, string):
        if len(string) < 2 or len(string) > 3:
            return False
        if not string[0] == 'C':
            return False
        if not string[1:].isdigit():
            return False
        return True

    def is_pdb_coil(self, string):
        return is_pdb_address(string)


class PDBLight(object):
    """Base class for lights connected to a PD-8x8 driver board."""

    def __init__(self, pdb, number_str):
        self.pdb = pdb
        upper_str = number_str.upper()
        if self.is_direct_lamp(upper_str):
            self.lamp_type = 'dedicated'
            self.output = int(number_str[1:])
        elif self.is_pdb_lamp(number_str):
            # C-Ax-By-z:R-Ax-By-z  or  C-x/y/z:R-x/y/z
            self.lamp_type = 'pdb'
            source_addr, sink_addr = self.split_matrix_addr_parts(number_str)
            (self.source_boardnum, self.source_banknum, self.source_outputnum) = decode_pdb_address(source_addr)
            (self.sink_boardnum, self.sink_banknum, self.sink_outputnum) = decode_pdb_address(sink_addr)
        else:
            self.lamp_type = 'unknown'

    def source_board(self):
        return self.source_boardnum

    def sink_board(self):
        return self.sink_boardnum

    def source_bank(self):
        return self.source_boardnum * 2 + self.source_banknum

    def sink_bank(self):
        return self.sink_boardnum * 2 + self.sink_banknum

    def source_output(self):
        return self.source_outputnum

    def sink_output(self):
        return self.sink_outputnum

    def dedicated_output(self):
        return self.output

    def is_direct_lamp(self, string):
        if len(string) < 2 or len(string) > 3:
            return False
        if not string[0] == 'L':
            return False
        if not string[1:].isdigit():
            return False
        return True

    def split_matrix_addr_parts(self, string):
        """ Input is of form C-Ax-By-z:R-Ax-By-z  or  C-x/y/z:R-x/y/z  or
        aliasX:aliasY.  We want to return only the address part: Ax-By-z,
        x/y/z, or aliasX.  That is, remove the two character prefix if present.
        """
        addrs = string.rsplit(':')
        if len(addrs) is not 2:
            return []
        addrs_out = []
        for addr in addrs:
            bits = addr.split('-')
            if len(bits) is 1:
                addrs_out.append(addr)  # Append unchanged.
            else:  # Generally this will be len(bits) 2 or 4.
                # Remove the first bit and rejoin.
                addrs_out.append('-'.join(bits[1:]))
        return addrs_out

    def is_pdb_lamp(self, string):
        params = self.split_matrix_addr_parts(string)
        if len(params) != 2:
            return False
        for addr in params:
            if not is_pdb_address(addr):
                return False
        return True


class PDBLED(RGBLEDPlatformInterface):
    """Represents an RGB LED connected to a PD-LED board."""

    def __init__(self, board, address, proc_driver, invert=False):
        self.log = logging.getLogger('PDBLED')
        self.board = board
        self.address = address
        self.proc = proc_driver
        self.invert = invert

        # todo make sure self.address is a 3-element list

        self.log.debug("Creating PD-LED item: board: %s, "
                       "RGB outputs: %s", self.board,
                       self.address)

    def color(self, color):
        """Instantly sets this LED to the color passed.

        Args:
            color: an RGBColor object
        """

        # self.log.debug("Setting Color. Board: %s, Address: %s, Color: %s",
        #               self.board, self.address, color)

        self.proc.led_color(self.board, self.address[0], color.red)
        self.proc.led_color(self.board, self.address[1], color.green)
        self.proc.led_color(self.board, self.address[2], color.blue)

    def disable(self):
        """Disables (turns off) this LED instantly. For multi-color LEDs it
        turns all elements off.
        """
        self.color(RGBColor())

    def enable(self):
        """Enables (turns on) this LED instantly. For multi-color LEDs it turns
        all elements on.
        """
        self.color(RGBColor('White'))


def is_pdb_address(addr):
    """Returns True if the given address is a valid PDB address."""
    try:
        decode_pdb_address(addr=addr)
        return True
    except ValueError:
        return False


def decode_pdb_address(addr):
    """Decodes Ax-By-z or x/y/z into PDB address, bank number, and output
    number.

    Raises a ValueError exception if it is not a PDB address, otherwise returns
    a tuple of (addr, bank, number).

    """
    if '-' in addr:  # Ax-By-z form
        params = addr.rsplit('-')
        if len(params) != 3:
            raise ValueError('pdb address must have 3 components')
        board = int(params[0][1:])
        bank = int(params[1][1:])
        output = int(params[2][0:])
        return board, bank, output

    elif '/' in addr:  # x/y/z form
        params = addr.rsplit('/')
        if len(params) != 3:
            raise ValueError('pdb address must have 3 components')
        board = int(params[0])
        bank = int(params[1])
        output = int(params[2])
        return board, bank, output

    else:
        raise ValueError('PDB address delimiter (- or /) not found.')


class PROCSwitch(object):
    def __init__(self, config, number, notify_on_debounce):
        self.log = logging.getLogger('PROCSwitch')
        self.config = config
        self.number = number
        self.notify_on_debounce = notify_on_debounce
        self.hw_rules = {"closed_debounced": [],
                         "closed_nondebounced": [],
                         "open_debounced": [],
                         "open_nondebounced": []}


class PROCDriver(DriverPlatformInterface):
    """ Base class for drivers connected to a P3-ROC. This class is used for all
    drivers, regardless of whether they're connected to a P-ROC driver board
    (such as the PD-16 or PD-8x8) or an OEM driver board.

    """

    def __init__(self, number, proc_driver, config, machine):
        self.log = logging.getLogger('PROCDriver')
        self.number = number
        self.proc = proc_driver
        self.machine = machine
        self.config = config

        self.log.debug("Driver Settings for %s", self.number)

    def get_pwm_on_ms(self, coil):
        # figure out what kind of enable we need:
        if coil.config['hold_power']:
            pwm_on_ms, pwm_off_ms = (Util.pwm8_to_on_off(coil.config['hold_power']))
            del pwm_off_ms
            return pwm_on_ms

        elif coil.config['pwm_on_ms'] and coil.config['pwm_off_ms']:
            return int(coil.config['pwm_on_ms'])
        else:
            return 0

    def get_pwm_off_ms(self, coil):
        # figure out what kind of enable we need:
        if coil.config['hold_power']:
            pwm_on_ms, pwm_off_ms = (Util.pwm8_to_on_off(coil.config['hold_power']))
            del pwm_on_ms
            return pwm_off_ms

        elif coil.config['pwm_on_ms'] and coil.config['pwm_off_ms']:
            return int(coil.config['pwm_off_ms'])
        else:
            return 0

    def get_pulse_ms(self, coil):
        if coil.config['pulse_ms']:
            return int(coil.config['pulse_ms'])
        else:
            return self.machine.config['mpf']['default_pulse_ms']

    def disable(self, coil):
        """Disables (turns off) this driver."""
        del coil
        self.log.debug('Disabling Driver')
        self.proc.driver_disable(self.number)

    def enable(self, coil):
        """Enables (turns on) this driver."""
        if self.get_pwm_on_ms(coil) and self.get_pwm_off_ms(coil):
            self.log.debug('Enabling. Initial pulse_ms:%s, pwm_on_ms: %s'
                           'pwm_off_ms: %s',
                           self.get_pwm_on_ms(coil),
                           self.get_pwm_off_ms(coil),
                           self.get_pulse_ms(coil))

            self.proc.driver_patter(self.number,
                                    self.get_pwm_on_ms(coil),
                                    self.get_pwm_off_ms(coil),
                                    self.get_pulse_ms(coil), True)
        else:
            self.log.debug('Enabling at 100%')

            if not coil.config['allow_enable']:
                raise AssertionError("Received a command to enable this coil "
                                     "without pwm, but 'allow_enable' has not been"
                                     "set to True in this coil's configuration.")

            self.proc.driver_schedule(number=self.number, schedule=0xffffffff,
                                      cycle_seconds=0, now=True)

    def pulse(self, coil, milliseconds):
        """Enables this driver for `milliseconds`.

        ``ValueError`` will be raised if `milliseconds` is outside of the range
        0-255.
        """
        del coil

        self.log.debug('Pulsing for %sms', milliseconds)
        self.proc.driver_pulse(self.number, milliseconds)

        return milliseconds

    def state(self):
        """Returns a dictionary representing this driver's current
        configuration state.
        """
        return self.proc.driver_get_state(self.number)


class PROCMatrixLight(MatrixLightPlatformInterface):
    def __init__(self, number, proc_driver):
        self.log = logging.getLogger('PROCMatrixLight')
        self.number = number
        self.proc = proc_driver

    def off(self):
        """Disables (turns off) this driver."""
        self.proc.driver_disable(self.number)

    def on(self, brightness=255):
        """Enables (turns on) this driver."""
        if brightness >= 255:
            self.proc.driver_schedule(number=self.number, schedule=0xffffffff,
                                      cycle_seconds=0, now=True)
        elif brightness == 0:
            self.off()
        else:
            pass
            # patter rates of 10/1 through 2/9

        """
        Koen's fade code he posted to pinballcontrollers:
        def mode_tick(self):
            if self.fade_counter % 10 == 0:
                for lamp in self.game.lamps:
                    if lamp.name.find("gi0") == -1:
                        var = 4.0*math.sin(0.02*float(self.fade_counter)) + 5.0
                        on_time = 11-round(var)
                        off_time = round(var)
                        lamp.patter(on_time, off_time)
                self.fade_counter += 1
        """     # pylint: disable=W0105
