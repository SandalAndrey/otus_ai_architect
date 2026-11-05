// Облачные ресурсы этапа 1. Описание - docs/16-delivery-pipeline.md, раздел 1.
//
// СКЕЛЕТ. Не применялся: terraform apply не выполнялся ни разу, модуль
// ./modules/minio не написан, значения переменных не заданы. Файл фиксирует
// состав ресурсов и принятые решения. Применяется с работы 3.1 плана работ.

terraform {
  required_version = ">= 1.9"

  required_providers {
    yandex = {
      source  = "yandex-cloud/yandex"
      version = "~> 0.129"
    }
  }

  // Состояние хранится в объектном хранилище с блокировкой, а не локально.
  // Иначе два применения с разных машин расходятся, и инфраструктура
  // перестаёт описываться кодом фактически, оставаясь описанной формально.
  backend "s3" {
    bucket = "assistant-tfstate"
    key    = "stage1/terraform.tfstate"
    region = "ru-central1"
  }
}

provider "yandex" {
  zone = var.zone // Регион РФ: NFR-3.5 требует хранения на территории РФ
}

// --- Сеть -------------------------------------------------------------------

resource "yandex_vpc_network" "core" {
  name = "assistant-net"
}

// Публичный периметр: только обратный прокси и панели наблюдаемости.
resource "yandex_vpc_subnet" "edge" {
  name           = "edge"
  network_id     = yandex_vpc_network.core.id
  zone           = var.zone
  v4_cidr_blocks = ["10.10.1.0/24"]
}

// Служебная подсеть: хранилища, сервис инференса, конвейер данных.
// NAT отсутствует намеренно - закрытый контур обеспечивается маршрутизацией,
// а не договорённостью не обращаться к внешним API. Достаточно одной
// библиотеки, которая пойдёт за обновлением, чтобы договорённость нарушилась.
resource "yandex_vpc_subnet" "internal" {
  name           = "internal"
  network_id     = yandex_vpc_network.core.id
  zone           = var.zone
  v4_cidr_blocks = ["10.10.2.0/24"]
}

// --- Узел инференса ---------------------------------------------------------

// Конфигурация из ADR-0019: одна A100 40 ГБ, Qwen3-14B в INT4, vLLM.
// Выбор обоснован полосой памяти, а не объёмом: 14B даёт 108 ток/с при
// требуемых 90, 30B помещается в карту, но даёт 50.
resource "yandex_compute_instance" "inference" {
  name        = "assistant-gpu"
  platform_id = var.gpu_platform_id
  zone        = var.zone

  resources {
    cores  = 8
    memory = 96
    gpus   = 1
  }

  boot_disk {
    initialize_params {
      image_id = var.gpu_image_id // образ с драйверами и CUDA
      size     = 200              // веса моделей и кэш образов
      type     = "network-ssd"
    }
  }

  secondary_disk {
    disk_id = yandex_compute_disk.data.id
  }

  network_interface {
    subnet_id = yandex_vpc_subnet.internal.id
    nat       = false
  }

  metadata = {
    user-data = file("${path.module}/cloud-init.yaml")
  }
}

// Данные на отдельном диске: пересоздание узла не должно их уносить.
resource "yandex_compute_disk" "data" {
  name = "assistant-data"
  size = 500
  type = "network-ssd"
  zone = var.zone
}

// --- Объектное хранилище ----------------------------------------------------

// Зоны озера из ADR-0011 плюс бакет трасс для Tempo и бакет резервных копий.
// На этапе 1 это MinIO внутри контура: управляемый облачный S3 исключён
// наравне с облачными LLM-API.
module "object_storage" {
  source = "./modules/minio" // модуль ещё не написан

  buckets = ["bronze", "silver", "gold", "traces", "backups"]

  // Bronze хранит оригиналы байт в байт, включая закупочные цены до подмены:
  // версионирование включено, доступ только у конвейера.
  versioning = {
    bronze = true
    silver = false
    gold   = false
  }

  retention = {
    traces  = "30d"
    backups = "90d"
  }
}

// --- Секреты ----------------------------------------------------------------

// Terraform создаёт секрет и права на него, значения кладутся отдельно.
// Состояние Terraform содержит значения ресурсов в открытом виде, поэтому
// секрет, записанный переменной, окажется в состоянии.
resource "yandex_lockbox_secret" "app" {
  name = "assistant-secrets"
}
