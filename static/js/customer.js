(() => {
  const $ = id => document.getElementById(id);
  const direction = $("direction"), departure = $("departure"), arrival = $("arrival");
  const dateInput = $("travel-date"), timeInput = $("travel-time");
  let fare = 0, inserted = [], routeConfirmed = false;
  const routeData = window.KIOSK_ROUTES || {};
  const money = value => `${Number(value).toLocaleString("ru-RU")} ₽`;

  function show(id, text, type = "") {
    const el = $(id); el.textContent = text; el.className = `inline-feedback ${type}`;
  }
  function display(message, sub = "") {
    $("display-message").textContent = message;
    $("display-sub").textContent = sub;
  }
  function updatePayment() {
    const sum = inserted.reduce((a, b) => a + b, 0);
    $("inserted").textContent = money(sum);
    $("remaining").textContent = routeConfirmed ? money(Math.max(0, fare - sum)) : "—";
    $("pay-button").disabled = !routeConfirmed || sum < fare;
    $("cancel-button").disabled = inserted.length === 0;
  }
  function resetRoute() {
    routeConfirmed = false; fare = 0; inserted = [];
    $("fare").textContent = "—";
    $("ticket-result").classList.add("hidden");
    $("pay-button").disabled = true;
    updatePayment();
  }

  direction.addEventListener("change", () => {
    departure.innerHTML = '<option value="">Выберите пункт отправления</option>';
    (routeData[direction.value] || []).forEach(name => departure.add(new Option(name, name)));
    departure.disabled = !direction.value;
    arrival.innerHTML = '<option value="">Сначала выберите отправление</option>';
    arrival.disabled = true;
    resetRoute();
    display("Выберите пункт отправления");
  });
  departure.addEventListener("change", () => {
    arrival.innerHTML = '<option value="">Выберите пункт прибытия</option>';
    (routeData[direction.value] || []).filter(x => x !== departure.value)
      .forEach(name => arrival.add(new Option(name, name)));
    arrival.disabled = !departure.value;
    resetRoute();
    display("Выберите пункт прибытия");
  });
  [arrival, dateInput, timeInput].forEach(el => el.addEventListener("change", resetRoute));

  $("confirm-route").addEventListener("click", () => {
    if (!direction.value || !departure.value || !arrival.value || !dateInput.value || !timeInput.value) {
      show("route-feedback", "Заполните все поля маршрута.", "error"); return;
    }
    const chosen = new Date(`${dateInput.value}T00:00:00`);
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const limit = new Date(today); limit.setDate(limit.getDate() + 10);
    if (chosen < today || chosen > limit) {
      show("route-feedback", "Дата должна быть в пределах ближайших 10 суток.", "error"); return;
    }
    const stations = routeData[direction.value];
    fare = Math.max(5, 20 + Math.abs(stations.indexOf(arrival.value) - stations.indexOf(departure.value)) * 15);
    routeConfirmed = true;
    inserted = [];
    $("fare").textContent = money(fare);
    $("ticket-result").classList.add("hidden");
    show("route-feedback", "Маршрут подтверждён. Внесите необходимую сумму.", "success");
    display("Введите деньги", `${departure.value} — ${arrival.value}. Стоимость: ${money(fare)}.`);
    updatePayment();
  });

  document.querySelectorAll(".denomination").forEach(button => {
    button.addEventListener("click", () => {
      if (!routeConfirmed) { show("payment-feedback", "Сначала подтвердите маршрут.", "error"); return; }
      inserted.push(Number(button.dataset.value));
      const sum = inserted.reduce((a, b) => a + b, 0);
      updatePayment();
      if (sum < fare) {
        show("payment-feedback", `Введённой суммы недостаточно. Не хватает ${money(fare - sum)}.`, "error");
        display("Введённой суммы недостаточно", `Внесено ${money(sum)}.`);
      } else {
        show("payment-feedback", "Сумма достаточна. Можно завершить оплату.", "success");
        display("Сумма достаточна", "Нажмите «Оплатить и напечатать».");
      }
    });
  });

  $("pay-button").addEventListener("click", async () => {
    if (!routeConfirmed) return;
    $("pay-button").disabled = true;
    try {
      const response = await fetch("/api/purchase", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          direction: direction.value, departure: departure.value, arrival: arrival.value,
          travel_date: dateInput.value, travel_time: timeInput.value, payment: inserted
        })
      });
      const result = await response.json();
      if (!response.ok || !result.ok) {
        show("payment-feedback", result.message || "Не удалось выполнить покупку.", "error");
        display(result.refund ? "Заберите деньги" : "Покупка не выполнена", result.message || "");
        if (result.refund) { inserted = []; routeConfirmed = false; updatePayment(); }
        else updatePayment();
        return;
      }
      const t = result.ticket;
      $("ticket-result").classList.remove("hidden");
      $("ticket-result").innerHTML = `
        <h3>БИЛЕТ НА ЭЛЕКТРИЧКУ</h3>
        <small>АИС · Терминал № 020 · № ${t.id}</small>
        <div class="route">${t.departure} → ${t.arrival}</div>
        <div>Направление: <b>${t.direction}</b></div>
        <div>Дата поездки: <b>${new Date(t.travel_date + "T00:00:00").toLocaleDateString("ru-RU")}</b></div>
        <div>Отправление: <b>${t.travel_time}</b></div>
        <div style="margin-top:8px">Стоимость: <b>${money(t.price)}</b></div>
        <div class="ticket-bottom">Покупка: ${t.purchased_at}</div>`;
      const changeText = result.change ? ` Заберите сдачу: ${money(result.change)}.` : "";
      show("payment-feedback", result.message + changeText, "success");
      display("Заберите билет", result.change ? `Заберите также сдачу ${money(result.change)}.` : "Оплата прошла успешно.");
      inserted = []; routeConfirmed = false; fare = 0;
      $("pay-button").disabled = true; $("cancel-button").disabled = true;
      $("remaining").textContent = "—"; $("inserted").textContent = "0 ₽";
      $("fare").textContent = "—";
    } catch (error) {
      show("payment-feedback", "Нет связи с сервером. Проверьте, запущено ли приложение.", "error");
      updatePayment();
    }
  });

  $("cancel-button").addEventListener("click", () => {
    const sum = inserted.reduce((a, b) => a + b, 0);
    inserted = []; routeConfirmed = false; fare = 0;
    $("fare").textContent = "—";
    $("ticket-result").classList.add("hidden");
    show("payment-feedback", `Возврат выполнен. Заберите деньги: ${money(sum)}.`, "success");
    display("Заберите деньги", "Покупка отменена.");
    updatePayment();
  });
})();
