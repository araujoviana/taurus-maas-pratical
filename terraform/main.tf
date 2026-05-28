terraform {
  required_providers {
    huaweicloud = {
      source  = "huaweicloud/huaweicloud"
      version = "~> 1.67"
    }
  }
}

provider "huaweicloud" {
  region     = var.hw_region
  access_key = var.hw_access_key
  secret_key = var.hw_secret_key
}

# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------

data "huaweicloud_images_image" "ubuntu2204" {
  name        = "Ubuntu 22.04 server 64bit"
  most_recent = true
  visibility  = "public"
}

# ---------------------------------------------------------------------------
# Key pair
# ---------------------------------------------------------------------------

resource "huaweicloud_compute_keypair" "taurus-demo-key" {
  name       = "taurus-demo-key"
  public_key = file(var.ssh_public_key_path)
}

# ---------------------------------------------------------------------------
# Networking
# ---------------------------------------------------------------------------

resource "huaweicloud_vpc" "taurus-demo-vpc" {
  name = "taurus-demo-vpc"
  cidr = var.vpc_cidr
}

resource "huaweicloud_vpc_subnet" "taurus-demo-subnet" {
  name       = "taurus-demo-subnet"
  vpc_id     = huaweicloud_vpc.taurus-demo-vpc.id
  cidr       = var.subnet_cidr
  gateway_ip = "10.0.X.1"
  dns_list   = ["100.125.1.250", "100.125.21.250"]
}

# ---------------------------------------------------------------------------
# Security group
# ---------------------------------------------------------------------------

resource "huaweicloud_networking_secgroup" "taurus-demo-sg" {
  name = "taurus-demo-sg"
}

resource "huaweicloud_networking_secgroup_rule" "allow-ssh" {
  security_group_id = huaweicloud_networking_secgroup.taurus-demo-sg.id
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 22
  port_range_max    = 22
  remote_ip_prefix  = "0.0.0.0/0"
}

resource "huaweicloud_networking_secgroup_rule" "allow-http" {
  security_group_id = huaweicloud_networking_secgroup.taurus-demo-sg.id
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 80
  port_range_max    = 80
  remote_ip_prefix  = "0.0.0.0/0"
}

# Port 8000 (uvicorn) is NOT exposed publicly — nginx on port 80 is the only
# entry point. uvicorn binds to 127.0.0.1 only (see systemd service template).

# MySQL 3306: only resources attached to the same security group can connect
# (i.e. the ECS demo runner). No internet exposure for the database.
resource "huaweicloud_networking_secgroup_rule" "allow-mysql-internal" {
  security_group_id  = huaweicloud_networking_secgroup.taurus-demo-sg.id
  direction          = "ingress"
  ethertype          = "IPv4"
  protocol           = "tcp"
  port_range_min     = 3306
  port_range_max     = 3306
  remote_group_id    = huaweicloud_networking_secgroup.taurus-demo-sg.id
}

# ---------------------------------------------------------------------------
# ECS demo runner
# ---------------------------------------------------------------------------

resource "huaweicloud_compute_instance" "taurus-demo-runner" {
  name               = "taurus-demo-runner"
  flavor_id          = var.ecs_flavor
  image_id           = data.huaweicloud_images_image.ubuntu2204.id
  availability_zone  = var.availability_zone
  key_pair           = huaweicloud_compute_keypair.taurus-demo-key.name
  security_group_ids = [huaweicloud_networking_secgroup.taurus-demo-sg.id]

  network {
    uuid = huaweicloud_vpc_subnet.taurus-demo-subnet.id
  }

  user_data = <<-EOF
    #!/bin/bash
    apt-get update -qq
    apt-get install -y -qq python3-pip python3-venv nginx mysql-client
  EOF
}

# ---------------------------------------------------------------------------
# EIP
# ---------------------------------------------------------------------------

resource "huaweicloud_vpc_eip" "taurus-demo-eip" {
  publicip {
    type = "5_bgp"
  }

  bandwidth {
    name        = "taurus-demo-bw"
    size        = 10
    share_type  = "PER"
    charge_mode = "traffic"
  }
}

resource "huaweicloud_compute_eip_associate" "taurus-demo-eip-assoc" {
  public_ip   = huaweicloud_vpc_eip.taurus-demo-eip.address
  instance_id = huaweicloud_compute_instance.taurus-demo-runner.id
}

# ---------------------------------------------------------------------------
# TaurusDB (GaussDB MySQL)
# ---------------------------------------------------------------------------

resource "huaweicloud_gaussdb_mysql_instance" "taurus-demo-db" {
  name                  = "taurus-demo-db"
  flavor                = var.taurus_flavor
  password              = var.taurus_password
  vpc_id                = huaweicloud_vpc.taurus-demo-vpc.id
  subnet_id             = huaweicloud_vpc_subnet.taurus-demo-subnet.id
  security_group_id     = huaweicloud_networking_secgroup.taurus-demo-sg.id
  availability_zone_mode = "multi"
  master_availability_zone = var.availability_zone
  read_replicas         = 1
  time_zone             = "UTC+08:00"
  enterprise_project_id = "0"

  datastore {
    engine  = "gaussdb-mysql"
    version = "8.0"
  }
}

# ---------------------------------------------------------------------------
# TaurusDB proxy
# ---------------------------------------------------------------------------

resource "huaweicloud_gaussdb_mysql_proxy" "taurus-demo-proxy" {
  instance_id = huaweicloud_gaussdb_mysql_instance.taurus-demo-db.id
  flavor      = "gaussdb.proxy.xlarge.x86.2"
  node_num    = 2
  proxy_name  = "taurus-demo-proxy"
}
