.PHONY: up down seed logs tf-apply gen-inventory ansible-deploy hooks

hooks:
	git config core.hooksPath .githooks
	chmod +x .githooks/pre-commit .githooks/pre-push .githooks/check_secrets.py
	@echo "✅  Git hooks installed — commits/pushes are now scanned for credentials."

up: tf-apply gen-inventory ansible-deploy
	@echo ""
	@echo "✅  Dashboard: http://$$(cd terraform && terraform output -raw demo_runner_public_ip)"
	@echo "    Login: admin / $$(grep '^DEMO_PASSWORD=' .env | cut -d= -f2-)"

tf-apply:
	cd terraform && terraform init -upgrade && terraform apply -auto-approve

gen-inventory:
	uv run python scripts/gen_inventory.py

ansible-deploy:
	ansible-playbook -i ansible/inventory.ini ansible/site.yml

seed:
	ansible -i ansible/inventory.ini demo_runner -m shell \
	  -a "cd /opt/taurus-demo && uv run python scripts/seed_data.py"

logs:
	ansible -i ansible/inventory.ini demo_runner -m shell \
	  -a "journalctl -u taurus-demo -f --no-pager"

down:
	cd terraform && terraform destroy -auto-approve
