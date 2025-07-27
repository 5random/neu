#!/bin/bash
# 0. edit the script to your needs
# 1. copy content to your home directory:		nano ~/setup_script.sh
# 2. make script executable: 					chmod +x setup_script.sh
# 3. run script:								~/setup_script.sh

update_system () {
	sudo apt update
	sudo apt upgrade -y
	sudo apt autoremove -y
}

setup_firewall () {
	sudo apt install ufw
	sudo ufw default deny incoming
	sudo ufw default allow outgoing
    sudo ufw allow ssh
    sudo ufw allow 8080/tcp
	sudo ufw limit ssh
	sudo ufw show added
	sudo ufw enable
	sudo ufw status numbered
}

setup_ssh () {
	local FILE="/etc/ssh/sshd_config"
	if [ ! -f $FILE.bak ]; then sudo cp $FILE $FILE.bak; else sudo cp $FILE.bak $FILE; fi
	echo '
Port 22
PermitRootLogin no
PasswordAuthentication no
PermitEmptyPasswords no
X11Forwarding no
ClientAliveInterval 300
ClientAliveCountMax 3
Protocol 2
	' | sudo tee -a $FILE > /dev/null
}

clone_git () {
	local CVD="https://github.com/5random/neu.git"
	git clone $CVD ~/cvd_tracker
}

setup_software () {
	sudo apt install -y python3 python3-pip python3-dev python3-venv git v4l-utils
    clone_git
	sudo pip3 install -r ~/cvd_tracker/requirements.txt
    
}


setup_file_strucutre () { #todo select templates or solution + read password
	PS3="select the operation: "
	local items=("load template files (student)" "load solution files (tutor/wimi)")
	COLUMNS=1
	select ITEM in "${items[@]}" "quit"; do
		case $ITEM in 
			"load template files (student)")
			clone_git;load_template_files_git;break;;
			"load solution files (tutor/wimi)")
			load_solution_files_kvweb;break;;
			"quit")
			break;;
			*) 
			echo "invalid option $REPLY";break;;
		esac
	done	
}

setup_autostart () {
	local SCRIPT="/home/$CVD/main.py"
	local FILE="/etc/systemd/system/cvd_tracker.service"
	sudo touch $FILE
	echo '
[Unit]
Description="Autostart CVD_Tracker"
After=multi-user.target
[Service]
Type=idle
ExecStart=/usr/bin/python3 ${SCRIPT}
[Install]
WantedBy=multi-user.target
	' | sudo tee $FILE > /dev/null
	sudo chmod 644 $FILE
	sudo systemctl daemon-reload
	sudo systemctl enable $FILE
}

setup_eduroam () {
	local CERT="https://doku.tid.dfn.de/_media/de:dfnpki:ca:dfn-verein_community_root_ca_2022.pem"
	sudo mkdir -p /etc/ssl/certs
	sudo wget -O /etc/ssl/certs/tuhh_eduroam_certificate.pem $CERT
}

full_setup () {
	update_system
	setup_firewall
	setup_ssh
	setup_software
	setup_autostart
	setup_eduroam
	question="reboot system now [y/n]? "; if yn_question; then sudo /sbin/reboot; fi
}

question="continue? [y/n] "
yn_question () {
	while true; do
		read -p "$question" YN
		case $YN in
			[Yy]* ) return 0;;
			[Nn]* ) return 1;;
			* ) echo "please answer yes/y or no/n.";;
		esac
	done
}

echo "running CVD_Tracker setup script."

PS3="select the operation: "
items=("update system" "setup firewall" "setup ssh" "setup software" "setup autostart script" "setup eduroam" "full setup" "reboot" "power off")
while true; do
	COLUMNS=1
	select ITEM in "${items[@]}" "quit"; do
		case $ITEM in 
			"update system")
			question="update your system [y/n]? "; if yn_question; then update_system; fi;break;;
			"setup firewall")
			question="setup a firewall with limited ssh connectivity (ipv4/6: port 22/tcp) [y/n]? "; if yn_question; then setup_firewall; fi;break;;
			"setup ssh")
			question="setup ssh config [y/n]? "; if yn_question; then setup_ssh; fi;break;;
			"setup software")
			question="setup software [y/n]? "; if yn_question; then setup_software; fi;break;;
			"setup files")
			question="setup autostart script (~/$TEAM/main.py) [y/n]? "; if yn_question; then setup_autostart; fi;break;;
			"setup eduroam")
			question="setup eduroam wifi connection [y/n]? "; if yn_question; then setup_eduroam; fi;break;;
			"full setup")
			question="start full setup [y/n]? "; if yn_question; then full_setup; fi;break;;
			"reboot")
			question="reboot system [y/n]? "; if yn_question; then sudo /sbin/reboot; fi;break;;
			"power off")
			question="turn off system [y/n]? "; if yn_question; then sudo poweroff; fi;break;;
			"quit")
			exit 0;;
			*) 
			echo "invalid option $REPLY";break;;
		esac
	done
done
echo "CVD_Tracker setup script finished."

