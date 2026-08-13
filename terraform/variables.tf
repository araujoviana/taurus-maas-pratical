variable "hw_access_key" {
  description = "Huawei Cloud Access Key"
  type        = string
  sensitive   = true
}

variable "hw_secret_key" {
  description = "Huawei Cloud Secret Key"
  type        = string
  sensitive   = true
}

variable "hw_region" {
  description = "Huawei Cloud region"
  type        = string
  default     = "ap-southeast-3"
}

variable "hw_project_id" {
  description = "Huawei Cloud project ID"
  type        = string
}

variable "taurus_password" {
  description = "TaurusDB master + demouser password (same as DEMO_PASSWORD in .env). Supply via terraform.tfvars or TF_VAR_taurus_password — no default so Terraform fails fast if omitted."
  type        = string
  sensitive   = true
}

variable "ssh_public_key_path" {
  description = "Path to SSH public key for ECS access"
  type        = string
  default     = "~/.ssh/taurus_demo_key.pub"
}

variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
  default     = "10.0.0.0/16"
}

variable "subnet_cidr" {
  description = "Subnet CIDR block"
  type        = string
  default     = "10.0.1.0/24"
}

variable "ecs_flavor" {
  description = "ECS flavor for demo runner"
  type        = string
  default     = "c6.xlarge.2"
}

variable "taurus_flavor" {
  description = "TaurusDB node flavor"
  type        = string
  default     = "gaussdb.mysql.xlarge.x86.4"
}

variable "availability_zone" {
  description = "Primary availability zone"
  type        = string
  default     = "ap-southeast-3a"
}

variable "availability_zone_2" {
  description = "Secondary availability zone for HA standby"
  type        = string
  default     = "ap-southeast-3b"
}
